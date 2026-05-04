"""Polling trigger for `review-requested:<operator>` searches on GitHub.

The trigger sleeps `poll_interval_seconds` between observations, calls
`gh.search_review_requested`, applies the §5 case table from
`data-model.md` to the union of "PRs returned now" and "PRs we have a
state row for", and emits `gh.review_requested` events for cases (1, 2, 3)
in the same SQLite transaction as the state UPSERT — so a crash between
INSERT and UPSERT can't drop an event nor double-fire one.

Events fan out into the outbox keyed by
`source_dedup_key = sha256("gh-review-requested|{repo}#{pr}@{sha}#{gen}")`.
The `events.UNIQUE(source, source_dedup_key)` constraint makes a polled
re-emit of the *same* `(head_sha, gen)` a no-op — even if two pollers
ever raced (they cannot in this single-process daemon).

Error mapping:
    AuthError      → re-raise (halts the daemon, exit 78)
    RateLimitError → skip the cycle, sleep one extra interval
    other transient/permanent → log + continue (next cycle retries)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Callable
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
from daeyeon_bot.infra.pr_review_state import StateRow, upsert_observation

_log = structlog.get_logger(__name__)

_HANDLER_NAME = "pr_review"
_SOURCE = "gh_review_requested"
_EVENT_TYPE = "gh.review_requested"
_REPO_URL_MARKER = "/repos/"

MANIFEST = TriggerManifest(
    name="gh_review_requested",
    source=_SOURCE,
    retryable_at_source=False,
)

StorageFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]


@dataclass(slots=True)
class GhReviewRequestedTrigger:
    """Long-running poller for `review-requested:<operator>` PRs."""

    gh: Any
    storage_factory: StorageFactory
    github_username: str
    poll_interval_seconds: float
    clock: Clock
    manifest: TriggerManifest = MANIFEST

    async def run(self, emit: EmitFn, ctx: TriggerContext) -> None:
        """Loop until cancelled. AuthError propagates and halts the daemon."""
        del emit, ctx  # this trigger persists events directly via storage_factory.
        while True:
            await asyncio.sleep(self.poll_interval_seconds)
            try:
                emitted = await self.poll_once()
            except AuthError:
                raise
            except RateLimitError:
                _log.warning("gh_review_requested.rate_limited")
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(self.poll_interval_seconds)
                continue
            except (TransientError, PermanentError) as exc:
                _log.warning("gh_review_requested.poll_failed", error=str(exc))
                continue
            if emitted:
                _log.info("gh_review_requested.emitted", count=emitted)

    async def poll_once(self) -> int:
        """One observe-and-emit pass. Returns the number of events emitted."""
        items = await self.gh.search_review_requested(self.github_username)
        observed: set[tuple[str, int]] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            pair = _parse_search_item(cast("dict[str, Any]", raw))
            if pair is not None:
                observed.add(pair)

        head_shas = await self._fetch_head_shas(observed)

        emitted = 0
        async with self.storage_factory() as conn:
            persisted = await _select_all_state(conn)
            now = self.clock.now()
            now_iso = now.astimezone(UTC).isoformat()
            keys = sorted(observed | set(persisted))
            for repo, pr_number in keys:
                in_now = (repo, pr_number) in observed
                head_sha = head_shas.get((repo, pr_number)) if in_now else None
                if in_now and head_sha is None:
                    # pr_get failed for this PR. Skip the row this cycle so a
                    # transient hiccup can't be misread as withdrawal (CASE 5)
                    # for an already-persisted PR. Next cycle retries.
                    continue
                gen, should_emit = await upsert_observation(
                    conn,
                    repo=repo,
                    pr_number=pr_number,
                    observed_now=in_now,
                    head_sha=head_sha,
                    now_iso=now_iso,
                )
                if should_emit and head_sha is not None:
                    if await _emit_event(
                        conn,
                        repo=repo,
                        pr_number=pr_number,
                        head_sha=head_sha,
                        request_gen=gen,
                        now=now,
                        now_iso=now_iso,
                    ):
                        emitted += 1
            await conn.commit()
        return emitted

    async def _fetch_head_shas(self, observed: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
        out: dict[tuple[str, int], str] = {}
        for repo, pr_number in observed:
            try:
                payload = await self.gh.pr_get(repo, pr_number)
            except (AuthError, RateLimitError):
                raise
            except (PermanentError, TransientError) as exc:
                _log.warning(
                    "gh_review_requested.pr_get_failed",
                    repo=repo,
                    pr_number=pr_number,
                    error=str(exc),
                )
                continue
            sha = _extract_head_sha(payload)
            if sha is not None:
                out[(repo, pr_number)] = sha
        return out


def _parse_search_item(item: dict[str, Any]) -> tuple[str, int] | None:
    number = item.get("number")
    repo_url = item.get("repository_url")
    if not isinstance(number, int) or not isinstance(repo_url, str):
        return None
    idx = repo_url.find(_REPO_URL_MARKER)
    if idx < 0:
        return None
    repo = repo_url[idx + len(_REPO_URL_MARKER) :]
    if "/" not in repo or not repo:
        return None
    return (repo, number)


def _extract_head_sha(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    head = payload.get("head")
    if not isinstance(head, dict):
        return None
    sha = head.get("sha")
    if isinstance(sha, str) and sha:
        return sha
    return None


async def _select_all_state(
    conn: aiosqlite.Connection,
) -> dict[tuple[str, int], StateRow]:
    out: dict[tuple[str, int], StateRow] = {}
    async with conn.execute(
        "SELECT repo, pr_number, head_sha, request_gen, in_pending_set,"
        " last_observed_at FROM gh_review_requested_state"
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        repo = str(row["repo"])
        pr_number = int(row["pr_number"])
        out[(repo, pr_number)] = StateRow(
            repo=repo,
            pr_number=pr_number,
            head_sha=str(row["head_sha"]),
            request_gen=int(row["request_gen"]),
            in_pending_set=bool(row["in_pending_set"]),
            last_observed_at=str(row["last_observed_at"]),
        )
    return out


async def _emit_event(
    conn: aiosqlite.Connection,
    *,
    repo: str,
    pr_number: int,
    head_sha: str,
    request_gen: int,
    now: Any,
    now_iso: str,
) -> bool:
    payload: dict[str, Any] = {
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "request_gen": str(request_gen),
        "requested_at": now_iso,
    }
    # A `request_gen > 1` means the operator re-requested review at the
    # same head SHA (or a new SHA arrived). Either way the handler must
    # supersede the prior `posted` audit row instead of skipping with
    # `already_reviewed`. `force=True` is the existing supersede signal.
    if request_gen > 1:
        payload["force"] = True
    seed = f"gh-review-requested|{repo}#{pr_number}@{head_sha}#{request_gen}"
    dedup_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    event = make_event(type=_EVENT_TYPE, payload=payload, created_at=now)
    inserted = await outbox.insert_event(conn, event, source=_SOURCE, source_dedup_key=dedup_key)
    if not inserted:
        return False
    await outbox.enqueue_handler(conn, event_id=event.id, handler=_HANDLER_NAME, now=now)
    return True


__all__ = ["MANIFEST", "GhReviewRequestedTrigger", "StorageFactory"]
