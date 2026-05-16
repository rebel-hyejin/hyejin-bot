"""Polling trigger for Jira tickets assigned to the operator or to a team.

Mirrors `gh_review_requested` shape but keyed on `issue_key`. Each poll
cycle:

  1. Run JQL `(assignee = currentUser() OR "Team" = "<team>")
     AND project IN (<allowed>) AND summary ~ "regression-test"
     AND status != Closed` (with pagination up to `max_per_cycle`).
  2. Build `page_now` from the result, classifying each issue as
     `user`- or `team`-matched by re-reading its assignee / team field.
  3. Snapshot persisted `jira_assigned_state` rows where
     `in_pending_set = 1`.
  4. For each issue in (page_now union page_prev), apply the §5 case table
     (`infra.jira_triage_state.upsert_observation`) and emit a
     `jira.assigned` event into the outbox for CASE 1 / CASE 2 only.

Cold-start (FR-004a): when `meta.jira_assigned_state_seeded != '1'`,
the first poll seeds every observed issue with `in_pending_set=1` but
does NOT emit any events. The marker flips to `'1'` afterward — no
retroactive thundering-herd on day-1 deploy.

Errors:
  AuthError      → re-raise (halts daemon, exit 78)
  RateLimitError → sleep Retry-After if available, then continue
  Transient/Permanent → log + continue
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC
from typing import Any, cast

import aiosqlite
import structlog

from daeyeon_bot.core.errors import (
    AuthError,
    PermanentError,
    RateLimitError,
    TransientError,
)
from daeyeon_bot.core.events import make_event
from daeyeon_bot.core.manifest import TriggerManifest
from daeyeon_bot.core.protocols import EmitFn, TriggerContext
from daeyeon_bot.core.time import Clock
from daeyeon_bot.infra import outbox
from daeyeon_bot.infra.jira_client import IssueSummary
from daeyeon_bot.infra.jira_triage_state import (
    seed_cold_start,
    seed_marker_is_set,
    seed_marker_set,
    upsert_observation,
)

_log = structlog.get_logger(__name__)

_HANDLER_NAME = "jira_triage"
_SOURCE = "jira_assigned"
_EVENT_TYPE = "jira.assigned"

MANIFEST = TriggerManifest(
    name="jira_assigned",
    source=_SOURCE,
    retryable_at_source=False,
)

StorageFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]
PermanentFailureReporter = Callable[[str], Awaitable[bool]]

_TITLE_RE = re.compile(r"^regression-test\s*\.\s*[\w.-]+\s*\.\s*TC-\d+-\S+\s*$")


def _never_paused() -> bool:
    return False


@dataclass(slots=True)
class JiraAssignedTrigger:
    """Long-running poller for `(assignee = me OR team = X)` Jira issues."""

    jira: Any
    storage_factory: StorageFactory
    jira_account_id: str
    allowed_projects: tuple[str, ...]
    team_name: str
    team_field_id: str  # may be empty if team_name is empty
    issuetype_name: str  # discovered at boot — e.g. "Bug"
    poll_interval_seconds: float
    max_per_cycle: int
    clock: Clock
    manifest: TriggerManifest = MANIFEST
    pause_check: Callable[[], bool] = _never_paused
    permanent_failure_reporter: PermanentFailureReporter | None = None

    async def run(self, emit: EmitFn, ctx: TriggerContext) -> None:
        """Loop until cancelled. AuthError propagates and halts the daemon."""
        del emit, ctx  # trigger persists events directly via storage_factory.
        while True:
            if self.pause_check():
                _log.info("jira_assigned.paused")
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            try:
                outcome = await self.poll_once()
            except AuthError:
                raise
            except RateLimitError as exc:
                _log.warning("jira_assigned.rate_limited", error=str(exc))
            except TransientError as exc:
                _log.warning("jira_assigned.poll_failed", error=str(exc))
            except PermanentError as exc:
                _log.warning("jira_assigned.poll_failed", error=str(exc))
                if (
                    self.permanent_failure_reporter is not None
                    and await self.permanent_failure_reporter(str(exc))
                ):
                    _log.error("jira_assigned.quarantined", error=str(exc))
                    return
            else:
                if outcome.seeded:
                    _log.info("jira_assigned.cold_start_seeded", count=outcome.seeded)
                if outcome.emitted:
                    _log.info("jira_assigned.emitted", count=outcome.emitted)
            await asyncio.sleep(self.poll_interval_seconds)

    async def poll_once(self) -> _PollOutcome:
        """One observe-and-emit pass."""
        # 1. Run paginated JQL.
        all_summaries = await self._fetch_all_pages()
        # 2. Classify into now-set with assignee path.
        now_set: dict[str, tuple[str, str]] = {}  # issue_key -> (project, path)
        for s in all_summaries:
            if not _TITLE_RE.match(s.summary):
                continue
            project = s.key.split("-", 1)[0]
            path = self._classify_path(s)
            now_set[s.key] = (project, path)

        async with self.storage_factory() as conn:
            now = self.clock.now()
            now_iso = now.astimezone(UTC).isoformat()

            # Cold-start: seed only.
            if not await seed_marker_is_set(conn):
                inserted = await seed_cold_start(
                    conn,
                    observed=[(key, project) for key, (project, _path) in now_set.items()],
                    now_iso=now_iso,
                )
                await seed_marker_set(conn)
                await conn.commit()
                _log.info("jira_assigned.cold_start_seed", seeded=inserted)
                return _PollOutcome(emitted=0, seeded=inserted)

            # Normal pass — apply state machine.
            persisted = await _select_pending_state(conn)
            keys_to_visit = sorted(set(now_set.keys()) | persisted)
            emitted = 0
            for issue_key in keys_to_visit:
                in_now = issue_key in now_set
                project: str
                path: str
                if in_now:
                    project, path = now_set[issue_key]
                else:
                    # Issue left the set; we still need to flip its
                    # in_pending_set flag. Use the project recorded on the
                    # row (read separately if needed — we pass the known
                    # project=split). For dormant CASE-4, the helper just
                    # needs *a* project; ignore mismatch.
                    project = issue_key.split("-", 1)[0]
                    path = ""
                gen, should_emit = await upsert_observation(
                    conn,
                    issue_key=issue_key,
                    project=project,
                    observed_now=in_now,
                    now_iso=now_iso,
                )
                if should_emit:
                    if await _emit_event(
                        conn,
                        issue_key=issue_key,
                        project=project,
                        assignment_gen=gen,
                        assignee_path=path,
                        now=now,
                        now_iso=now_iso,
                    ):
                        emitted += 1
            await conn.commit()
            return _PollOutcome(emitted=emitted, seeded=0)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _build_jql(self) -> str:
        # Quote each allowed project.
        project_clause = f"project IN ({', '.join(f'"{p}"' for p in self.allowed_projects)})"
        # Assignee / team OR group.
        if self.team_name and self.team_field_id:
            assignee_clause = f'(assignee = currentUser() OR "Team" = "{self.team_name}")'
        else:
            assignee_clause = "assignee = currentUser()"
        return (
            f"{assignee_clause} AND {project_clause}"
            f' AND issuetype = "{self.issuetype_name}"'
            ' AND summary ~ "regression-test"'
            " AND status != Closed"
        )

    async def _fetch_all_pages(self) -> list[IssueSummary]:
        out: list[IssueSummary] = []
        jql = self._build_jql()
        page_size = 50
        token: str | None = None
        while True:
            page = await self.jira.search_jql(
                jql=jql,
                fields=[
                    "key",
                    "summary",
                    "created",
                    "issuetype",
                    "assignee",
                    "status",
                    "parent",
                    self.team_field_id or "summary",  # cheap dedup if no team field
                ],
                next_page_token=token,
                max_results=page_size,
            )
            out.extend(page.issues)
            if len(out) >= self.max_per_cycle:
                _log.warning(
                    "jira_assigned.max_per_cycle_hit",
                    cap=self.max_per_cycle,
                    collected=len(out),
                )
                return out[: self.max_per_cycle]
            if page.next_page_token is None:
                return out
            token = page.next_page_token

    def _classify_path(self, summary: IssueSummary) -> str:
        """Determine whether the match came from the user or team clause.

        Reads the issue's assignee + team-field directly. If assignee
        matches the daemon's account_id → "user"; else if team field
        matches the configured `team_name` → "team"; else "team" as a
        permissive fallback (we know JQL admitted it via the OR group).
        """
        if summary.assignee_account_id == self.jira_account_id:
            return "user"
        if self.team_field_id and self.team_name:
            team_val = summary.raw_fields.get(self.team_field_id)
            if isinstance(team_val, dict):
                team_typed = cast("dict[str, Any]", team_val)
                if (
                    str(team_typed.get("name", "")) == self.team_name
                    or str(team_typed.get("id", "")) == self.team_name
                ):
                    return "team"
            elif isinstance(team_val, str) and team_val == self.team_name:
                return "team"
        return "team"  # permissive — JQL OR group admitted it


@dataclass(frozen=True, slots=True)
class _PollOutcome:
    emitted: int
    seeded: int


# ── Storage helpers ──────────────────────────────────────────────────────────


async def _select_pending_state(conn: aiosqlite.Connection) -> set[str]:
    out: set[str] = set()
    async with conn.execute(
        "SELECT issue_key FROM jira_assigned_state WHERE in_pending_set = 1"
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        out.add(str(row["issue_key"]))
    return out


async def _emit_event(
    conn: aiosqlite.Connection,
    *,
    issue_key: str,
    project: str,
    assignment_gen: int,
    assignee_path: str,
    now: Any,
    now_iso: str,
) -> bool:
    payload: dict[str, Any] = {
        "issue_key": issue_key,
        "project": project,
        "assignment_gen": assignment_gen,
        "assignee_path": assignee_path,
        "observed_at": now_iso,
    }
    seed = f"jira-assigned|{issue_key}|{assignment_gen}"
    dedup_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    event = make_event(type=_EVENT_TYPE, payload=payload, created_at=now)
    inserted = await outbox.insert_event(conn, event, source=_SOURCE, source_dedup_key=dedup_key)
    if not inserted:
        return False
    await outbox.enqueue_handler(conn, event_id=event.id, handler=_HANDLER_NAME, now=now)
    return True


__all__ = [
    "MANIFEST",
    "JiraAssignedTrigger",
    "PermanentFailureReporter",
    "StorageFactory",
]
