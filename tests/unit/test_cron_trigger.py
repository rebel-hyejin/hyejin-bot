"""cron trigger — feature 003 tests.

Drives `CronTrigger.tick_once()` directly (so we don't manage the run-loop's
sleep timing) against a real `aiosqlite` `tmp_path` DB. Verifies the
fire-once-per-local-day contract, the schedule-time gate, and that a queued
event lands in the outbox routed to the configured handler.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from hyejin_bot.infra import cron_state
from hyejin_bot.infra.storage import apply_migrations, open_db
from hyejin_bot.triggers.cron import CronTrigger


@dataclass(slots=True)
class _FixedClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, *, hours: int = 0, days: int = 0) -> None:
        self.current = self.current + timedelta(hours=hours, days=days)

    def monotonic(self) -> float:  # pragma: no cover - unused by the trigger
        return 0.0


async def _open(tmp_path: Path) -> aiosqlite.Connection:
    conn = await open_db(tmp_path / "state.db")
    await apply_migrations(conn)
    return conn


def _storage_factory(db_path: Path) -> Any:
    @asynccontextmanager  # type: ignore[arg-type, misc]
    async def _factory():  # type: ignore[no-untyped-def]
        conn = await open_db(db_path)
        try:
            yield conn
        finally:
            await conn.close()

    return _factory


def _make_trigger(*, db_path: Path, clock: Any, hour: int = 8, minute: int = 30) -> CronTrigger:
    return CronTrigger(
        job_name="news_daily",
        event_type="news.daily",
        handler_name="news",
        schedule_hour=hour,
        schedule_minute=minute,
        timezone_name="Asia/Seoul",
        storage_factory=_storage_factory(db_path),
        clock=clock,
        poll_interval_seconds=300,
    )


async def _outbox_rows(conn: aiosqlite.Connection) -> list[tuple[str, str]]:
    async with conn.execute(
        "SELECT e.type, o.handler FROM outbox o JOIN events e ON e.id = o.event_id"
    ) as cur:
        rows = await cur.fetchall()
    return [(str(r["type"]), str(r["handler"])) for r in rows]


# KST is UTC+9, so 23:45 UTC on the 22nd is 08:45 KST on the 23rd — past 08:30.
_KST_MORNING_UTC = datetime(2026, 6, 22, 23, 45, 0, tzinfo=UTC)  # 08:45 KST on the 23rd


@pytest.mark.asyncio
async def test_fires_once_after_schedule_time(tmp_path: Path) -> None:
    conn = await _open(tmp_path)
    clock = _FixedClock(current=_KST_MORNING_UTC)
    trigger = _make_trigger(db_path=tmp_path / "state.db", clock=clock)

    assert await trigger.tick_once() is True

    rows = await _outbox_rows(conn)
    assert rows == [("news.daily", "news")]
    assert await cron_state.last_fired_date(conn, job_name="news_daily") == "2026-06-23"
    await conn.close()


@pytest.mark.asyncio
async def test_does_not_fire_before_schedule_time(tmp_path: Path) -> None:
    conn = await _open(tmp_path)
    # 22:00 UTC on the 22nd == 07:00 KST on the 23rd — before 08:30 KST.
    clock = _FixedClock(current=datetime(2026, 6, 22, 22, 0, 0, tzinfo=UTC))
    trigger = _make_trigger(db_path=tmp_path / "state.db", clock=clock)

    assert await trigger.tick_once() is False
    assert await _outbox_rows(conn) == []
    await conn.close()


@pytest.mark.asyncio
async def test_second_tick_same_day_is_noop(tmp_path: Path) -> None:
    conn = await _open(tmp_path)
    clock = _FixedClock(current=_KST_MORNING_UTC)
    trigger = _make_trigger(db_path=tmp_path / "state.db", clock=clock)

    assert await trigger.tick_once() is True
    # A few hours later, same KST calendar day.
    clock.advance(hours=3)
    assert await trigger.tick_once() is False
    assert len(await _outbox_rows(conn)) == 1
    await conn.close()


@pytest.mark.asyncio
async def test_fires_again_next_day(tmp_path: Path) -> None:
    conn = await _open(tmp_path)
    clock = _FixedClock(current=_KST_MORNING_UTC)
    trigger = _make_trigger(db_path=tmp_path / "state.db", clock=clock)

    assert await trigger.tick_once() is True
    clock.advance(days=1)
    assert await trigger.tick_once() is True

    rows = await _outbox_rows(conn)
    assert rows == [("news.daily", "news"), ("news.daily", "news")]
    assert await cron_state.last_fired_date(conn, job_name="news_daily") == "2026-06-24"
    await conn.close()


@pytest.mark.asyncio
async def test_dedup_hit_still_marks_fired(tmp_path: Path) -> None:
    # Simulate an overlapping instance: fire once, then wipe THIS instance's
    # cron_state row (as if another daemon fired but we didn't see its state).
    # The next tick's event INSERT dedups on (source, dedup_key) → emit=False,
    # but cron_state must still be marked so we don't retry every tick all day.
    conn = await _open(tmp_path)
    clock = _FixedClock(current=_KST_MORNING_UTC)
    trigger = _make_trigger(db_path=tmp_path / "state.db", clock=clock)

    assert await trigger.tick_once() is True
    # Forget our own state but keep the emitted event (the dedup row).
    await conn.execute("DELETE FROM cron_state WHERE job_name = 'news_daily'")
    await conn.commit()

    # Next tick: event dedups (no new outbox row) but state is re-recorded.
    assert await trigger.tick_once() is False
    assert await cron_state.last_fired_date(conn, job_name="news_daily") == "2026-06-23"
    # And a subsequent tick is a clean no-op (state now suppresses it).
    assert await trigger.tick_once() is False
    assert len(await _outbox_rows(conn)) == 1  # only the original emit
    await conn.close()


@pytest.mark.asyncio
async def test_restart_same_day_does_not_refire(tmp_path: Path) -> None:
    # A daemon restart constructs a fresh trigger but shares the DB; the
    # persisted last_fired_date must still suppress a same-day re-emit.
    conn = await _open(tmp_path)
    clock = _FixedClock(current=_KST_MORNING_UTC)
    first = _make_trigger(db_path=tmp_path / "state.db", clock=clock)
    assert await first.tick_once() is True

    clock.advance(hours=2)
    restarted = _make_trigger(db_path=tmp_path / "state.db", clock=clock)
    assert await restarted.tick_once() is False
    assert len(await _outbox_rows(conn)) == 1
    await conn.close()
