"""End-to-end replay: handler dies → DLQ → operator replays → handler Acks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import pytest

from daeyeon_bot.app import replay as replay_mod
from daeyeon_bot.app.config import load
from daeyeon_bot.app.container import ContainerOverrides
from daeyeon_bot.app.lifecycle import BootOptions, boot
from daeyeon_bot.core.errors import PermanentError
from daeyeon_bot.core.events import make_event
from daeyeon_bot.infra import outbox, storage
from daeyeon_bot.infra.claude import FakeClaudeSession

pytestmark = pytest.mark.integration


class _RaisingClaudeSession:
    """Test double whose `query()` raises the configured exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> _RaisingClaudeSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def query(self, prompt: str, *, system: str | None = None) -> str:
        raise self._exc


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def config_file(tmp_path: Path, state_dir: Path) -> Path:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f"""
[runtime]
state_dir = {str(state_dir)!r}

[logging]
level = "WARNING"
format = "console"

[handlers.echo]
enabled = true

[routing]
"manual.message" = ["echo"]
""".lstrip(),
        encoding="utf-8",
    )
    return cfg_path


async def _wait_for_status(db_path: Path, *, event_id: str, status: str, attempts: int) -> bool:
    for _ in range(attempts):
        async with storage.connection(db_path) as conn:
            async with conn.execute(
                "SELECT status FROM outbox WHERE event_id = ?", (event_id,)
            ) as cur:
                row = await cur.fetchone()
        if row is not None and row["status"] == status:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_dlq_then_replay_acks(config_file: Path) -> None:
    # First boot: Claude raises permanent error → DLQ.
    failing = _RaisingClaudeSession(PermanentError("boom"))
    overrides = ContainerOverrides(claude_session_factory=lambda: failing)
    stop = asyncio.Event()
    cfg = load(str(config_file))

    boot_task = asyncio.create_task(
        boot(
            BootOptions(
                config_path=str(config_file),
                overrides=overrides,
                install_signal_handlers=False,
                external_stop_event=stop,
            )
        )
    )

    db_path = cfg.db_path
    for _ in range(50):
        if db_path.exists():
            break
        await asyncio.sleep(0.05)
    assert db_path.exists()

    now = datetime.now(tz=UTC)
    event = make_event(type="manual.message", payload={"message": "hello"}, created_at=now)
    async with storage.connection(db_path) as conn:
        await storage.apply_migrations(conn)
        await outbox.insert_event(
            conn, event, source="manual", source_dedup_key=f"replay-{uuid.uuid4()}"
        )
        await outbox.enqueue_handler(conn, event_id=event.id, handler="echo", now=now)
        await conn.commit()

    assert await _wait_for_status(db_path, event_id=event.id, status="dead_letter", attempts=100)

    stop.set()
    await asyncio.wait_for(boot_task, timeout=5.0)

    # Operator replays via the public API the CLI uses.
    async with storage.connection(db_path) as conn:
        plan = await replay_mod.replay(conn, event_id=event.id, now=datetime.now(tz=UTC))
    assert plan.committed
    assert len(plan.targets) == 1

    # Second boot: a healthy session finishes the replayed row.
    healthy = FakeClaudeSession(default="recovered-ok")
    overrides_2 = ContainerOverrides(claude_session_factory=lambda: healthy)
    stop_2 = asyncio.Event()

    boot_task_2 = asyncio.create_task(
        boot(
            BootOptions(
                config_path=str(config_file),
                overrides=overrides_2,
                install_signal_handlers=False,
                external_stop_event=stop_2,
            )
        )
    )
    try:
        assert await _wait_for_status(db_path, event_id=event.id, status="acked", attempts=200)
    finally:
        stop_2.set()
        await asyncio.wait_for(boot_task_2, timeout=5.0)

    # Audit row from manual_replay must remain alongside the dispatcher run.
    async with storage.connection(db_path) as conn:
        async with conn.execute(
            "SELECT triggered_by, status FROM runs WHERE event_id = ? ORDER BY id",
            (event.id,),
        ) as cur:
            rows = await cur.fetchall()
    triggered_by = [r["triggered_by"] for r in rows]
    statuses = [r["status"] for r in rows]
    assert replay_mod.REPLAY_TRIGGER in triggered_by
    assert "acked" in statuses
