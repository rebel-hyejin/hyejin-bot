"""TriggerSupervisor: sliding-window failure tracking + persistent quarantine."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from hyejin_bot.app.supervisor import (
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW,
    FailureWindow,
    TriggerSupervisor,
    is_quarantined,
    list_quarantined,
    unquarantine,
)
from hyejin_bot.infra import storage


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    conn = await storage.open_db(tmp_path / "state.db")
    await storage.apply_migrations(conn)
    try:
        yield conn
    finally:
        await conn.close()


def test_failure_window_below_threshold_is_safe(now: datetime) -> None:
    win = FailureWindow(threshold=5, window=timedelta(minutes=10))
    for i in range(4):
        tripped = win.record_failure(now + timedelta(seconds=i))
        assert tripped is False


def test_failure_window_trips_at_threshold(now: datetime) -> None:
    win = FailureWindow(threshold=3, window=timedelta(minutes=10))
    assert win.record_failure(now) is False
    assert win.record_failure(now + timedelta(seconds=1)) is False
    assert win.record_failure(now + timedelta(seconds=2)) is True


def test_failure_window_evicts_outside_window(now: datetime) -> None:
    win = FailureWindow(threshold=3, window=timedelta(minutes=10))
    win.record_failure(now)
    win.record_failure(now + timedelta(minutes=1))
    win.record_failure(now + timedelta(minutes=2))
    # 11 minutes later, the first three are evicted; one new failure should not trip.
    tripped = win.record_failure(now + timedelta(minutes=15))
    assert tripped is False


def test_failure_window_reset_clears_state(now: datetime) -> None:
    win = FailureWindow(threshold=3, window=timedelta(minutes=10))
    win.record_failure(now)
    win.record_failure(now + timedelta(seconds=1))
    win.reset()
    assert win.record_failure(now + timedelta(seconds=2)) is False


async def test_supervisor_writes_quarantine_row_on_trip(
    conn: aiosqlite.Connection, now: datetime
) -> None:
    sup = TriggerSupervisor(threshold=3, window=timedelta(minutes=10))
    for i in range(2):
        tripped = await sup.record_failure(
            conn, trigger_name="cron-flaky", reason="boom", at=now + timedelta(seconds=i)
        )
        assert tripped is False
        assert await is_quarantined(conn, trigger_name="cron-flaky") is False

    tripped = await sup.record_failure(
        conn, trigger_name="cron-flaky", reason="boom", at=now + timedelta(seconds=2)
    )
    assert tripped is True
    assert await is_quarantined(conn, trigger_name="cron-flaky") is True

    rows = await list_quarantined(conn)
    assert len(rows) == 1
    assert rows[0]["trigger_name"] == "cron-flaky"
    assert rows[0]["reason"] == "boom"


async def test_supervisor_success_resets_window(conn: aiosqlite.Connection, now: datetime) -> None:
    sup = TriggerSupervisor(threshold=3, window=timedelta(minutes=10))
    await sup.record_failure(conn, trigger_name="cron-flaky", reason="x", at=now)
    await sup.record_failure(
        conn, trigger_name="cron-flaky", reason="x", at=now + timedelta(seconds=1)
    )
    sup.record_success("cron-flaky")
    # First failure post-reset should not trip.
    tripped = await sup.record_failure(
        conn, trigger_name="cron-flaky", reason="x", at=now + timedelta(seconds=2)
    )
    assert tripped is False


async def test_unquarantine_clears_rows(conn: aiosqlite.Connection, now: datetime) -> None:
    await conn.execute(
        "INSERT INTO quarantine(trigger_name, quarantined_at, reason) VALUES ('cron', ?, 'boom')",
        (now.isoformat(),),
    )
    await conn.commit()
    assert await is_quarantined(conn, trigger_name="cron") is True

    cleared = await unquarantine(conn, trigger_names=["cron"])
    assert cleared == 1
    assert await is_quarantined(conn, trigger_name="cron") is False


def test_default_thresholds_match_plan() -> None:
    """PLAN.md §5 Phase 2 quarantine policy: 5 fails / 10 min."""
    assert DEFAULT_THRESHOLD == 5
    assert DEFAULT_WINDOW == timedelta(minutes=10)
