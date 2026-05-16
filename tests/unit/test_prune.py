"""Retention pruning: dedup keys by expiry, runs by age + per-handler keep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from daeyeon_bot.app.config import (
    Config,
    HandlerEntry,
    LoggingSection,
    RetentionSection,
    RuntimeSection,
    SecretsSection,
)
from daeyeon_bot.app.prune import prune
from daeyeon_bot.infra import storage


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


def _config(
    tmp_path: Path, *, runs_days: int = 30, keep: int = 10, events_days: int = 90
) -> Config:
    return Config(
        runtime=RuntimeSection(state_dir=str(tmp_path)),
        logging=LoggingSection(),
        secrets=SecretsSection(),
        retention=RetentionSection(
            runs_days=runs_days,
            runs_keep_per_handler=keep,
            events_days=events_days,
        ),
        triggers={},
        handlers={"echo": HandlerEntry(enabled=True)},
        routing={},
    )


async def _open(db_path: Path) -> aiosqlite.Connection:
    conn = await storage.open_db(db_path)
    await storage.apply_migrations(conn)
    return conn


async def _insert_event(conn: aiosqlite.Connection, *, event_id: str, now: datetime) -> None:
    await conn.execute(
        """
        INSERT INTO events(id, type, source, source_dedup_key, payload_json, trace_id, created_at, schema_version)
        VALUES (?, 'manual.message', 'manual', ?, '{}', ?, ?, 1)
        """,
        (event_id, f"k-{event_id}", f"trace-{event_id}", now.isoformat()),
    )


async def _insert_outbox(
    conn: aiosqlite.Connection,
    *,
    event_id: str,
    handler: str,
    status: str = "pending",
    created_at: datetime | None = None,
) -> int:
    iso = (created_at or datetime(2026, 1, 1, tzinfo=UTC)).isoformat()
    cur = await conn.execute(
        """
        INSERT INTO outbox(event_id, handler, status, attempt, attempt_epoch,
                           created_at, updated_at)
        VALUES (?, ?, ?, 0, 0, ?, ?)
        """,
        (event_id, handler, status, iso, iso),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


async def _insert_run(
    conn: aiosqlite.Connection,
    *,
    outbox_id: int,
    event_id: str,
    handler: str,
    started_at: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO runs(outbox_id, event_id, handler, attempt_epoch, started_at,
                         finished_at, status, duration_ms, triggered_by, error)
        VALUES (?, ?, ?, 0, ?, ?, 'acked', 1, 'dispatcher', NULL)
        """,
        (outbox_id, event_id, handler, started_at.isoformat(), started_at.isoformat()),
    )


async def _insert_dedup(conn: aiosqlite.Connection, *, key: str, expires_at: datetime) -> None:
    await conn.execute(
        "INSERT INTO dedup_keys(key, expires_at) VALUES (?, ?)",
        (key, expires_at.isoformat()),
    )


async def test_prune_dedup_keys_removes_expired(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_dedup(conn, key="expired", expires_at=now - timedelta(seconds=1))
        await _insert_dedup(conn, key="future", expires_at=now + timedelta(days=1))
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path), now=now)
        assert report.dedup_keys_deleted == 1

        async with conn.execute("SELECT COUNT(*) AS n FROM dedup_keys") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["n"] == 1
    finally:
        await conn.close()


async def test_prune_runs_keeps_recent_per_handler(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_event(conn, event_id="ev-1", now=now)
        ob = await _insert_outbox(conn, event_id="ev-1", handler="echo")
        # 5 old runs (> cutoff), keep_per_handler=2 → 3 should be deleted.
        for i in range(5):
            await _insert_run(
                conn,
                outbox_id=ob,
                event_id="ev-1",
                handler="echo",
                started_at=now - timedelta(days=60, seconds=i),
            )
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path, runs_days=30, keep=2), now=now)
        assert report.runs_deleted == 3

        async with conn.execute("SELECT COUNT(*) AS n FROM runs") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["n"] == 2
    finally:
        await conn.close()


async def test_prune_runs_does_not_delete_recent_runs(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_event(conn, event_id="ev-1", now=now)
        ob = await _insert_outbox(conn, event_id="ev-1", handler="echo")
        for i in range(5):
            await _insert_run(
                conn,
                outbox_id=ob,
                event_id="ev-1",
                handler="echo",
                started_at=now - timedelta(seconds=i),
            )
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path, runs_days=30, keep=2), now=now)
        assert report.runs_deleted == 0
    finally:
        await conn.close()


async def test_prune_idempotent(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_dedup(conn, key="expired", expires_at=now - timedelta(seconds=1))
        await conn.commit()

        first = await prune(conn, config=_config(tmp_path), now=now)
        second = await prune(conn, config=_config(tmp_path), now=now)
        assert first.dedup_keys_deleted == 1
        assert second.dedup_keys_deleted == 0
    finally:
        await conn.close()


async def test_prune_events_drops_settled_old_events(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_event(conn, event_id="old", now=now - timedelta(days=120))
        await _insert_outbox(conn, event_id="old", handler="echo", status="acked")
        await _insert_event(conn, event_id="recent", now=now - timedelta(days=10))
        await _insert_outbox(conn, event_id="recent", handler="echo", status="acked")
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path, events_days=90), now=now)
        assert report.events_deleted == 1
        assert report.outbox_deleted == 1

        async with conn.execute("SELECT id FROM events ORDER BY id") as cur:
            rows = await cur.fetchall()
        ids = {row["id"] for row in rows}
        assert ids == {"recent"}
    finally:
        await conn.close()


async def test_prune_events_skips_active_outbox(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_event(conn, event_id="old-pending", now=now - timedelta(days=120))
        await _insert_outbox(conn, event_id="old-pending", handler="echo", status="pending")
        await _insert_event(conn, event_id="old-retry", now=now - timedelta(days=120))
        await _insert_outbox(conn, event_id="old-retry", handler="echo", status="retry")
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path, events_days=90), now=now)
        assert report.events_deleted == 0
        assert report.outbox_deleted == 0
    finally:
        await conn.close()


async def test_prune_events_handles_no_outbox_rows(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        # Old event with no outbox rows at all (e.g., no handler routed it).
        await _insert_event(conn, event_id="orphan", now=now - timedelta(days=120))
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path, events_days=90), now=now)
        assert report.events_deleted == 1
        assert report.outbox_deleted == 0
    finally:
        await conn.close()


async def _insert_gh_state(
    conn: aiosqlite.Connection,
    *,
    repo: str,
    pr_number: int,
    in_pending_set: int,
    last_observed_at: datetime,
) -> None:
    await conn.execute(
        "INSERT INTO gh_review_requested_state"
        "(repo, pr_number, head_sha, request_gen, in_pending_set, last_observed_at)"
        " VALUES (?, ?, 'abc1234', 1, ?, ?)",
        (repo, pr_number, in_pending_set, last_observed_at.isoformat()),
    )


async def test_prune_drops_dormant_gh_state_past_threshold(tmp_path: Path, now: datetime) -> None:
    """Dormant rows past `gh_state_dormant_days` go; recent dormant + pending rows stay."""
    conn = await _open(tmp_path / "state.db")
    try:
        # Dormant + ancient → should be deleted.
        await _insert_gh_state(
            conn,
            repo="o/r",
            pr_number=1,
            in_pending_set=0,
            last_observed_at=now - timedelta(days=120),
        )
        # Dormant but recent → should stay.
        await _insert_gh_state(
            conn,
            repo="o/r",
            pr_number=2,
            in_pending_set=0,
            last_observed_at=now - timedelta(days=7),
        )
        # Pending + ancient → never pruned (live request).
        await _insert_gh_state(
            conn,
            repo="o/r",
            pr_number=3,
            in_pending_set=1,
            last_observed_at=now - timedelta(days=365),
        )
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path), now=now)
        assert report.gh_state_deleted == 1

        async with conn.execute(
            "SELECT pr_number FROM gh_review_requested_state ORDER BY pr_number"
        ) as cur:
            rows = await cur.fetchall()
        assert {row["pr_number"] for row in rows} == {2, 3}
    finally:
        await conn.close()


# ── dead_letter aggressive pruning ───────────────────────────────────────────


async def test_prune_dead_letter_outbox_past_cutoff(tmp_path: Path, now: datetime) -> None:
    """`dead_letter` outbox rows past `dead_letter_days` get deleted independently
    of the `events_days` window."""
    conn = await _open(tmp_path / "state.db")
    try:
        # event itself is only 40 days old — still inside events_days=90 — but
        # its outbox row is `dead_letter` and older than `dead_letter_days=30`.
        await _insert_event(conn, event_id="dlq-old", now=now - timedelta(days=40))
        await _insert_outbox(
            conn,
            event_id="dlq-old",
            handler="echo",
            status="dead_letter",
            created_at=now - timedelta(days=40),
        )
        # And a fresh dead_letter that should stick around.
        await _insert_event(conn, event_id="dlq-fresh", now=now - timedelta(days=5))
        await _insert_outbox(
            conn,
            event_id="dlq-fresh",
            handler="echo",
            status="dead_letter",
            created_at=now - timedelta(days=5),
        )
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path), now=now)
        assert report.dead_letter_deleted == 1
        assert report.outbox_deleted == 1  # subsumed into outbox_deleted total

        async with conn.execute("SELECT event_id FROM outbox") as cur:
            rows = await cur.fetchall()
        remaining = {row["event_id"] for row in rows}
        assert "dlq-old" not in remaining
        assert "dlq-fresh" in remaining
    finally:
        await conn.close()


async def test_prune_dead_letter_does_not_touch_acked(tmp_path: Path, now: datetime) -> None:
    """Aggressive pruning only targets `dead_letter` — acked rows wait for events_days."""
    conn = await _open(tmp_path / "state.db")
    try:
        await _insert_event(conn, event_id="acked-old", now=now - timedelta(days=40))
        await _insert_outbox(
            conn,
            event_id="acked-old",
            handler="echo",
            status="acked",
            created_at=now - timedelta(days=40),
        )
        await conn.commit()

        report = await prune(conn, config=_config(tmp_path), now=now)
        assert report.dead_letter_deleted == 0
        async with conn.execute("SELECT COUNT(*) AS n FROM outbox") as cur:
            row = await cur.fetchone()
        assert row is not None and row["n"] == 1
    finally:
        await conn.close()
