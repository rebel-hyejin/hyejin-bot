"""jira_assigned trigger — T048 tests.

Drives FakeJira through the polling loop's state-machine cases via
direct `poll_once()` calls (so we don't need to manage the run-loop's
sleep timing). Real `aiosqlite` against `tmp_path` so the trigger's
SQL transactions land in a real DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from hyejin_bot.core.errors import AuthError
from hyejin_bot.infra.jira_triage_state import (
    get_state,
    seed_marker_is_set,
)
from hyejin_bot.infra.storage import apply_migrations, open_db
from hyejin_bot.triggers.jira_assigned import JiraAssignedTrigger
from tests.fakes.jira_client import FakeJiraClient


@dataclass(slots=True)
class _FixedClock:
    """Tick the clock forward via `advance()` between poll calls."""

    current: datetime = datetime(2026, 5, 13, 7, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, minutes: int = 0) -> None:
        from datetime import timedelta as _td

        self.current = self.current + _td(minutes=minutes)


async def _open(tmp_path: Path) -> aiosqlite.Connection:
    conn = await open_db(tmp_path / "state.db")
    await apply_migrations(conn)
    return conn


def _make_storage_factory(db_path: Path) -> Any:
    @asynccontextmanager  # type: ignore[arg-type, misc]
    async def _factory():  # type: ignore[no-untyped-def]
        conn = await open_db(db_path)
        try:
            yield conn
        finally:
            await conn.close()

    return _factory


def _make_trigger(
    *,
    db_path: Path,
    jira: Any,
    clock: Any,
    allowed_projects: tuple[str, ...] = ("SSWCI",),
    team_name: str = "DevOps",
    team_field_id: str = "customfield_10050",
) -> JiraAssignedTrigger:
    return JiraAssignedTrigger(
        jira=jira,
        storage_factory=_make_storage_factory(db_path),
        jira_account_id="557058:fake",
        allowed_projects=allowed_projects,
        team_name=team_name,
        team_field_id=team_field_id,
        issuetype_name="Bug",
        poll_interval_seconds=5,
        max_per_cycle=200,
        clock=clock,
    )


# ── Cold-start seed (FR-004a) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cold_start_seeds_without_emitting(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)  # creates the schema, then closes.
    await bootstrap.close()

    jira = FakeJiraClient()
    for k in ("SSWCI-100", "SSWCI-101"):
        jira.add_issue(
            key=k,
            summary=f"regression-test . ssw-giga-02 . TC-1-{k}",
            project="SSWCI",
            assignee_account_id="557058:fake",
        )
    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=jira, clock=clock)
    outcome = await trig.poll_once()
    assert outcome.seeded == 2
    assert outcome.emitted == 0

    # Marker now set; no events in outbox.
    conn = await open_db(db_path)
    try:
        assert await seed_marker_is_set(conn) is True
        async with conn.execute("SELECT COUNT(*) AS c FROM events") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["c"] == 0
    finally:
        await conn.close()


# ── Normal pass: CASE 1 emits ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_case1_first_observation_after_seed_emits(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)
    # Pre-set the seed marker so next poll doesn't enter cold-start.
    await bootstrap.execute("UPDATE meta SET value = '1' WHERE key = 'jira_assigned_state_seeded'")
    await bootstrap.commit()
    await bootstrap.close()

    jira = FakeJiraClient()
    jira.add_issue(
        key="SSWCI-100",
        summary="regression-test . ssw-giga-02 . TC-1-x",
        project="SSWCI",
        assignee_account_id="557058:fake",
    )
    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=jira, clock=clock)
    outcome = await trig.poll_once()
    assert outcome.emitted == 1

    # One row in jira_assigned_state, one event in outbox.
    conn = await open_db(db_path)
    try:
        state_row = await get_state(conn, "SSWCI-100")
        assert state_row is not None
        assert state_row.assignment_gen == 1
        assert state_row.in_pending_set is True
        async with conn.execute("SELECT type, source, source_dedup_key FROM events") as cur:
            rows = list(await cur.fetchall())
        assert len(rows) == 1
        assert rows[0]["type"] == "jira.assigned"
        assert rows[0]["source"] == "jira_assigned"
    finally:
        await conn.close()


# ── Title-regex non-match excluded ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_title_non_regression_failure_not_admitted(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)
    await bootstrap.execute("UPDATE meta SET value = '1' WHERE key = 'jira_assigned_state_seeded'")
    await bootstrap.commit()
    await bootstrap.close()

    jira = FakeJiraClient()
    jira.add_issue(
        key="SSWCI-200",
        summary="Some regular bug ticket",
        project="SSWCI",
        assignee_account_id="557058:fake",
    )
    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=jira, clock=clock)
    outcome = await trig.poll_once()
    # Not in regression-test format AND JQL also has `summary ~ "regression-test"`
    # but FakeJira applies that filter — let's check no event was emitted.
    assert outcome.emitted == 0


# ── Dedup: re-poll same (issue, gen) is no-op ─────────────────────────────────


@pytest.mark.asyncio
async def test_redundant_observation_is_no_op(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)
    await bootstrap.execute("UPDATE meta SET value = '1' WHERE key = 'jira_assigned_state_seeded'")
    await bootstrap.commit()
    await bootstrap.close()

    jira = FakeJiraClient()
    jira.add_issue(
        key="SSWCI-100",
        summary="regression-test . ssw-giga-02 . TC-1-x",
        project="SSWCI",
        assignee_account_id="557058:fake",
    )
    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=jira, clock=clock)
    o1 = await trig.poll_once()
    clock.advance(minutes=5)
    o2 = await trig.poll_once()
    assert o1.emitted == 1
    assert o2.emitted == 0


# ── Re-entry after leaving the set bumps gen ──────────────────────────────────


@pytest.mark.asyncio
async def test_reentry_increments_assignment_gen(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)
    await bootstrap.execute("UPDATE meta SET value = '1' WHERE key = 'jira_assigned_state_seeded'")
    await bootstrap.commit()
    await bootstrap.close()

    jira = FakeJiraClient()
    jira.add_issue(
        key="SSWCI-100",
        summary="regression-test . ssw-giga-02 . TC-1-x",
        project="SSWCI",
        assignee_account_id="557058:fake",
    )
    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=jira, clock=clock)
    # 1st poll → gen=1
    await trig.poll_once()
    # Withdraw the issue (un-assign).
    clock.advance(minutes=5)
    jira.update_assignee("SSWCI-100", account_id=None)
    out_drop = await trig.poll_once()
    assert out_drop.emitted == 0
    # Re-assign back.
    clock.advance(minutes=5)
    jira.update_assignee("SSWCI-100", account_id="557058:fake")
    out_reentry = await trig.poll_once()
    assert out_reentry.emitted == 1

    conn = await open_db(db_path)
    try:
        row = await get_state(conn, "SSWCI-100")
        assert row is not None
        assert row.assignment_gen == 2
    finally:
        await conn.close()


# ── AuthError propagates (halts daemon) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_error_propagates(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)
    await bootstrap.close()

    class _ExplodingJira:
        async def search_jql(self, **kwargs: Any) -> Any:
            raise AuthError("token expired")

    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=_ExplodingJira(), clock=clock)  # type: ignore[arg-type]
    with pytest.raises(AuthError):
        await trig.poll_once()


# ── max_per_cycle cap ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_per_cycle_caps_output(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    bootstrap = await _open(tmp_path)
    await bootstrap.close()

    jira = FakeJiraClient()
    # Seed 60 issues — but max_per_cycle is 50.
    for i in range(60):
        jira.add_issue(
            key=f"SSWCI-{1000 + i}",
            summary=f"regression-test . h . TC-1-{i}",
            project="SSWCI",
            assignee_account_id="557058:fake",
        )
    clock = _FixedClock()
    trig = _make_trigger(db_path=db_path, jira=jira, clock=clock)
    trig.max_per_cycle = 50

    outcome = await trig.poll_once()
    # Cold-start path → seeded count capped at 50.
    assert outcome.seeded <= 50
