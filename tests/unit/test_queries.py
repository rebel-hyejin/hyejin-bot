"""Read-only inspect query helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from hyejin_bot.core.events import make_event
from hyejin_bot.core.results import Ack
from hyejin_bot.infra import outbox, queries, storage


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


async def _open(db_path: Path) -> aiosqlite.Connection:
    conn = await storage.open_db(db_path)
    await storage.apply_migrations(conn)
    return conn


async def test_outbox_status_counts_zero_when_empty(tmp_path: Path) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        counts = await queries.outbox_status_counts(conn)
        assert set(counts.keys()) == set(queries.OUTBOX_STATUSES)
        assert all(v == 0 for v in counts.values())
    finally:
        await conn.close()


async def test_outbox_status_counts_groups_by_status(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        ev = make_event(type="manual.message", payload={}, created_at=now)
        await outbox.insert_event(conn, ev, source="manual", source_dedup_key="k1")
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="echo", now=now)
        await conn.commit()

        counts = await queries.outbox_status_counts(conn)
        assert counts["pending"] == 1
        assert counts["acked"] == 0
    finally:
        await conn.close()


async def test_list_events_returns_recent_first(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        e1 = make_event(type="manual.message", payload={"i": 1}, created_at=now)
        await outbox.insert_event(conn, e1, source="manual", source_dedup_key="k1")
        from datetime import timedelta

        e2 = make_event(
            type="manual.message", payload={"i": 2}, created_at=now + timedelta(seconds=1)
        )
        await outbox.insert_event(conn, e2, source="manual", source_dedup_key="k2")
        await conn.commit()

        rows = await queries.list_events(conn, limit=10)
        assert [r.id for r in rows] == [e2.id, e1.id]
    finally:
        await conn.close()


async def test_get_event_returns_none_for_missing(tmp_path: Path) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        assert await queries.get_event(conn, event_id="missing") is None
    finally:
        await conn.close()


async def test_get_event_returns_record(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        ev = make_event(type="manual.message", payload={"x": 1}, created_at=now)
        await outbox.insert_event(conn, ev, source="manual", source_dedup_key="k1")
        await conn.commit()

        rec = await queries.get_event(conn, event_id=ev.id)
        assert rec is not None
        assert rec.type == "manual.message"
        assert rec.payload["x"] == 1
    finally:
        await conn.close()


async def test_outbox_for_event_lists_rows(tmp_path: Path, now: datetime) -> None:
    conn = await _open(tmp_path / "state.db")
    try:
        ev = make_event(type="manual.message", payload={}, created_at=now)
        await outbox.insert_event(conn, ev, source="manual", source_dedup_key="k1")
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="echo", now=now)
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="other", now=now)
        await conn.commit()

        rows = await queries.outbox_for_event(conn, event_id=ev.id)
        assert {r.handler for r in rows} == {"echo", "other"}
    finally:
        await conn.close()


async def test_runs_for_event_returns_audit(tmp_path: Path, now: datetime) -> None:
    from datetime import timedelta

    conn = await _open(tmp_path / "state.db")
    try:
        ev = make_event(type="manual.message", payload={}, created_at=now)
        await outbox.insert_event(conn, ev, source="manual", source_dedup_key="k1")
        await outbox.enqueue_handler(conn, event_id=ev.id, handler="echo", now=now)
        await conn.commit()
        job = await outbox.claim_one(conn, claimed_by="proc-A", now=now)
        assert job is not None
        await outbox.settle(
            conn,
            job=job,
            result=Ack(),
            started_at=now,
            finished_at=now + timedelta(milliseconds=10),
            dedup_ttl=None,
        )

        runs = await queries.runs_for_event(conn, event_id=ev.id)
        assert len(runs) == 1
        assert runs[0].status == "acked"
    finally:
        await conn.close()


async def test_event_to_dict_round_trip(now: datetime) -> None:
    ev = make_event(type="manual.message", payload={"m": "hi"}, created_at=now)
    d = queries.event_to_dict(ev)
    assert d["id"] == ev.id
    assert d["type"] == "manual.message"
    assert d["payload"]["m"] == "hi"
    assert d["created_at"] == now.isoformat()
