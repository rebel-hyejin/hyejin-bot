"""Unit tests for `infra.pr_review_audit` against a real `aiosqlite` (T018)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from hyejin_bot.infra.pr_review_audit import (
    find_latest,
    insert_audit,
    list_for_pr,
    list_recent,
    record_supersede,
)
from hyejin_bot.infra.storage import apply_migrations, open_db


async def _seed_event(conn: aiosqlite.Connection, event_id: str = "evt-1") -> str:
    await conn.execute(
        "INSERT INTO events(id, type, schema_version, source, source_dedup_key,"
        " payload_json, trace_id, created_at)"
        " VALUES (?, 'pr.review.manual', 1, 'manual', ?, '{}', 'tr', ?)",
        (event_id, f"k-{event_id}", "2026-05-04T00:00:00+00:00"),
    )
    await conn.commit()
    return event_id


async def _migrated_db(tmp_path: Path) -> aiosqlite.Connection:
    conn = await open_db(tmp_path / "state.db")
    await apply_migrations(conn)
    return conn


@pytest.mark.asyncio
async def test_insert_posted_row_then_find_latest(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await _seed_event(conn)
        now = datetime.now(tz=UTC)
        audit_id = await insert_audit(
            conn,
            event_id="evt-1",
            repo="o/r",
            pr_number=42,
            head_sha="abc",
            request_gen="1",
            status="posted",
            review_id=999,
            submitted_at=now,
            summary_chars=200,
            inline_comment_count=3,
            persona_skill="pr-review",
            persona_mtime_ns=123,
            created_at=now,
        )
        await conn.commit()
        assert audit_id > 0

        latest = await find_latest(conn, "o/r", 42, "abc")
        assert latest is not None
        assert latest.status == "posted"
        assert latest.review_id == 999
        assert latest.persona_skill == "pr-review"
        assert latest.persona_mtime_ns == 123
        assert latest.superseded_review_ids == ()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_insert_skipped_self_authored(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await _seed_event(conn)
        now = datetime.now(tz=UTC)
        await insert_audit(
            conn,
            event_id="evt-1",
            repo="o/r",
            pr_number=42,
            head_sha="abc",
            request_gen="1",
            status="skipped_self_authored",
            created_at=now,
        )
        await conn.commit()
        latest = await find_latest(conn, "o/r", 42, "abc")
        assert latest is not None
        assert latest.status == "skipped_self_authored"
        assert latest.review_id is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_supersede_appends_old_review_id(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await _seed_event(conn)
        now = datetime.now(tz=UTC)
        audit_id = await insert_audit(
            conn,
            event_id="evt-1",
            repo="o/r",
            pr_number=42,
            head_sha="abc",
            request_gen="1",
            status="posted",
            review_id=111,
            submitted_at=now,
            created_at=now,
        )
        await conn.commit()

        new_submitted = datetime.now(tz=UTC)
        await record_supersede(conn, audit_id, new_review_id=222, new_submitted_at=new_submitted)
        await conn.commit()

        latest = await find_latest(conn, "o/r", 42, "abc")
        assert latest is not None
        assert latest.review_id == 222
        assert latest.superseded_review_ids == (111,)
        assert latest.status == "posted"

        # A second supersede chains the history.
        await record_supersede(
            conn,
            audit_id,
            new_review_id=333,
            new_submitted_at=datetime.now(tz=UTC),
        )
        await conn.commit()
        latest2 = await find_latest(conn, "o/r", 42, "abc")
        assert latest2 is not None
        assert latest2.review_id == 333
        assert latest2.superseded_review_ids == (111, 222)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_list_for_pr_and_list_recent_order_newest_first(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await _seed_event(conn, event_id="evt-1")
        await _seed_event(conn, event_id="evt-2")
        await _seed_event(conn, event_id="evt-3")
        now = datetime.now(tz=UTC)

        # Two rows on PR #1, one row on PR #2.
        await insert_audit(
            conn,
            event_id="evt-1",
            repo="o/r",
            pr_number=1,
            head_sha="aaa",
            request_gen="1",
            status="posted",
            review_id=10,
            submitted_at=now,
            created_at=now,
        )
        await insert_audit(
            conn,
            event_id="evt-2",
            repo="o/r",
            pr_number=1,
            head_sha="bbb",
            request_gen="2",
            status="posted",
            review_id=20,
            submitted_at=now,
            created_at=now,
        )
        await insert_audit(
            conn,
            event_id="evt-3",
            repo="o/r",
            pr_number=2,
            head_sha="ccc",
            request_gen="1",
            status="skipped_self_authored",
            created_at=now,
        )
        await conn.commit()

        for_pr1 = await list_for_pr(conn, repo="o/r", pr_number=1)
        assert [r.review_id for r in for_pr1] == [20, 10]

        for_pr2 = await list_for_pr(conn, repo="o/r", pr_number=2)
        assert [r.status for r in for_pr2] == ["skipped_self_authored"]

        all_recent = await list_recent(conn, limit=10)
        # Newest insert first: evt-3, evt-2, evt-1.
        assert [r.event_id for r in all_recent] == ["evt-3", "evt-2", "evt-1"]

        all_recent_capped = await list_recent(conn, limit=2)
        assert len(all_recent_capped) == 2
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_check_constraint_rejects_unknown_status(tmp_path: Path) -> None:
    conn = await _migrated_db(tmp_path)
    try:
        await _seed_event(conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO pr_review_audit(event_id, repo, pr_number, head_sha,"
                " request_gen, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("evt-1", "o/r", 1, "abc", "1", "BOGUS", "2026-01-01T00:00:00Z"),
            )
            await conn.commit()
    finally:
        await conn.close()
