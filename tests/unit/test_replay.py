"""Replay: dry-run plan, attempt_epoch++, runs audit row, running-row skip."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from daeyeon_bot.app import replay
from daeyeon_bot.core.events import make_event
from daeyeon_bot.core.results import DeadLetter
from daeyeon_bot.infra import outbox, storage


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


async def _open(db_path: Path) -> aiosqlite.Connection:
    conn = await storage.open_db(db_path)
    await storage.apply_migrations(conn)
    return conn


async def _insert_dead_letter(
    conn: aiosqlite.Connection, *, handler: str, now: datetime
) -> tuple[str, int]:
    """Insert an event + outbox row, claim, then DLQ. Returns (event_id, outbox_id)."""
    ev = make_event(type="manual.message", payload={"m": "hi"}, created_at=now)
    await outbox.insert_event(conn, ev, source="manual", source_dedup_key=f"k-{ev.id}")
    await outbox.enqueue_handler(conn, event_id=ev.id, handler=handler, now=now)
    await conn.commit()
    job = await outbox.claim_one(conn, claimed_by="proc-A", now=now)
    assert job is not None
    await outbox.settle(
        conn,
        job=job,
        result=DeadLetter(reason="boom"),
        started_at=now,
        finished_at=now,
        dedup_ttl=None,
    )
    return ev.id, job.outbox_id


async def test_plan_replay_returns_targets(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        event_id, _ = await _insert_dead_letter(conn, handler="echo", now=now)
        targets = await replay.plan_replay(conn, event_id=event_id)
        assert len(targets) == 1
        assert targets[0].handler == "echo"
        assert targets[0].current_status == "dead_letter"
        assert targets[0].current_attempt_epoch == 0
    finally:
        await conn.close()


async def test_plan_replay_with_handler_filter(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        ev = make_event(type="manual.message", payload={}, created_at=now)
        await outbox.insert_event(conn, ev, source="manual", source_dedup_key="k1")
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="echo", now=now)
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="other", now=now)
        await conn.commit()
        targets = await replay.plan_replay(conn, event_id=ev.id, handler="echo")
        assert len(targets) == 1
        assert targets[0].handler == "echo"
    finally:
        await conn.close()


async def test_replay_bumps_attempt_epoch_and_resets_status(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        event_id, outbox_id = await _insert_dead_letter(conn, handler="echo", now=now)

        plan = await replay.replay(conn, event_id=event_id, now=now + timedelta(seconds=1))
        assert plan.committed
        assert len(plan.targets) == 1

        async with conn.execute(
            "SELECT status, attempt, attempt_epoch, last_error, next_attempt_at"
            " FROM outbox WHERE id = ?",
            (outbox_id,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "pending"
        assert row["attempt"] == 0
        assert row["attempt_epoch"] == 1
        assert row["last_error"] is None
        assert row["next_attempt_at"] is None
    finally:
        await conn.close()


async def test_replay_writes_audit_run(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        event_id, outbox_id = await _insert_dead_letter(conn, handler="echo", now=now)
        await replay.replay(conn, event_id=event_id, now=now + timedelta(seconds=1))

        async with conn.execute(
            "SELECT status, triggered_by, attempt_epoch FROM runs"
            " WHERE outbox_id = ? AND status = ?",
            (outbox_id, replay.REPLAY_RUN_STATUS),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["triggered_by"] == replay.REPLAY_TRIGGER
        assert row["attempt_epoch"] == 1
    finally:
        await conn.close()


async def test_replay_skips_running_rows(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        ev = make_event(type="manual.message", payload={}, created_at=now)
        await outbox.insert_event(conn, ev, source="manual", source_dedup_key="k1")
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="echo", now=now)
        await conn.commit()
        # Claim it so it's in 'running' state
        job = await outbox.claim_one(conn, claimed_by="proc-A", now=now)
        assert job is not None

        plan = await replay.replay(conn, event_id=ev.id, now=now + timedelta(seconds=1))
        assert plan.empty is True

        # Outbox row still 'running', untouched
        async with conn.execute(
            "SELECT status, attempt_epoch FROM outbox WHERE id = ?", (job.outbox_id,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "running"
        assert row["attempt_epoch"] == 0
    finally:
        await conn.close()


async def test_replay_no_targets_when_event_unknown(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        plan = await replay.replay(conn, event_id="missing", now=now)
        assert plan.empty is True
    finally:
        await conn.close()
