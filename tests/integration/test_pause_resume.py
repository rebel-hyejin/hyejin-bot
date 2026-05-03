"""PAUSE flag halts new claims; resume re-enables dispatch."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daeyeon_bot.app import pause as pause_mod
from daeyeon_bot.app.config import load
from daeyeon_bot.app.container import ContainerOverrides
from daeyeon_bot.app.lifecycle import BootOptions, boot
from daeyeon_bot.core.events import make_event
from daeyeon_bot.infra import outbox, storage
from daeyeon_bot.infra.claude import FakeClaudeSession

pytestmark = pytest.mark.integration


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


async def test_pause_blocks_new_claims_resume_unblocks(config_file: Path, state_dir: Path) -> None:
    fake = FakeClaudeSession(default="paused-then-ok")
    overrides = ContainerOverrides(claude_session_factory=lambda: fake)
    stop = asyncio.Event()
    cfg = load(str(config_file))

    # Drop PAUSE before booting so the dispatcher starts in paused state.
    pause_mod.pause(cfg.pause_flag_path)

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
    event = make_event(type="manual.message", payload={"message": "hi"}, created_at=now)
    async with storage.connection(db_path) as conn:
        await storage.apply_migrations(conn)
        await outbox.insert_event(
            conn, event, source="manual", source_dedup_key=f"pause-{uuid.uuid4()}"
        )
        await outbox.enqueue_handler(conn, event_id=event.id, handler="echo", now=now)
        await conn.commit()

    # While paused, the row should NOT settle.
    settled_while_paused = await _wait_for_status(
        db_path, event_id=event.id, status="acked", attempts=10
    )
    assert settled_while_paused is False

    # Resume — dispatcher should pick the row up.
    pause_mod.resume(cfg.pause_flag_path)
    assert await _wait_for_status(db_path, event_id=event.id, status="acked", attempts=100)

    stop.set()
    await asyncio.wait_for(boot_task, timeout=5.0)
