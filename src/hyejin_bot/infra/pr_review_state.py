"""CRUD + state machine for `gh_review_requested_state` (data-model.md §5).

The polling trigger calls `upsert_observation()` once per `(repo, pr_number)`
in the union of "PRs in this poll" union "PRs we have a state row for". The
function returns `(request_gen, should_emit)` so the trigger can decide
whether to write an `events` row in the same transaction.

This module performs only the SELECT + INSERT/UPDATE; the caller manages
the surrounding transaction (so that the events INSERT and the state
UPSERT commit atomically — see `data-model.md` §5).
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True, slots=True)
class StateRow:
    """One row of `gh_review_requested_state`."""

    repo: str
    pr_number: int
    head_sha: str
    request_gen: int
    in_pending_set: bool
    last_observed_at: str


async def get_state(
    conn: aiosqlite.Connection,
    repo: str,
    pr_number: int,
) -> StateRow | None:
    """Return the persisted state row for `(repo, pr_number)`, or None."""
    async with conn.execute(
        "SELECT repo, pr_number, head_sha, request_gen, in_pending_set,"
        " last_observed_at FROM gh_review_requested_state"
        " WHERE repo = ? AND pr_number = ?",
        (repo, pr_number),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return StateRow(
        repo=str(row["repo"]),
        pr_number=int(row["pr_number"]),
        head_sha=str(row["head_sha"]),
        request_gen=int(row["request_gen"]),
        in_pending_set=bool(row["in_pending_set"]),
        last_observed_at=str(row["last_observed_at"]),
    )


async def upsert_observation(  # noqa: PLR0911 — explicit branch per §5 case
    conn: aiosqlite.Connection,
    *,
    repo: str,
    pr_number: int,
    observed_now: bool,
    head_sha: str | None,
    now_iso: str,
) -> tuple[int, bool]:
    """Apply the §5 case table for one `(repo, pr_number)` observation.

    Returns `(request_gen, should_emit)`:
        request_gen  — the *current* generation after this observation
        should_emit  — True if the trigger should INSERT an event for this PR
    """
    existing = await get_state(conn, repo, pr_number)

    # CASE 1: row IS NULL AND observed_now → INSERT, gen=1, emit.
    if existing is None and observed_now:
        if head_sha is None:
            raise ValueError("head_sha must be provided when observed_now=True")
        await conn.execute(
            "INSERT INTO gh_review_requested_state"
            "(repo, pr_number, head_sha, request_gen, in_pending_set,"
            " last_observed_at)"
            " VALUES (?, ?, ?, 1, 1, ?)",
            (repo, pr_number, head_sha, now_iso),
        )
        return (1, True)

    if existing is None:
        # row IS NULL AND NOT observed_now — never happens in the planned
        # callers (we only iterate over now_set union persisted), but keep it
        # well-defined so a stray call is a no-op rather than a crash.
        return (0, False)

    # CASE 2: was withdrawn, now observed → bump gen, flip pending=1, emit.
    if not existing.in_pending_set and observed_now:
        if head_sha is None:
            raise ValueError("head_sha must be provided when observed_now=True")
        new_gen = existing.request_gen + 1
        await conn.execute(
            "UPDATE gh_review_requested_state"
            " SET head_sha = ?, request_gen = ?, in_pending_set = 1,"
            " last_observed_at = ?"
            " WHERE repo = ? AND pr_number = ?",
            (head_sha, new_gen, now_iso, repo, pr_number),
        )
        return (new_gen, True)

    # CASE 3: pending and observed at a *new* head_sha → bump gen, emit.
    if (
        existing.in_pending_set
        and observed_now
        and head_sha is not None
        and existing.head_sha != head_sha
    ):
        new_gen = existing.request_gen + 1
        await conn.execute(
            "UPDATE gh_review_requested_state"
            " SET head_sha = ?, request_gen = ?, last_observed_at = ?"
            " WHERE repo = ? AND pr_number = ?",
            (head_sha, new_gen, now_iso, repo, pr_number),
        )
        return (new_gen, True)

    # CASE 4: pending and observed at the *same* head_sha → touch only.
    if existing.in_pending_set and observed_now:
        await conn.execute(
            "UPDATE gh_review_requested_state"
            " SET last_observed_at = ?"
            " WHERE repo = ? AND pr_number = ?",
            (now_iso, repo, pr_number),
        )
        return (existing.request_gen, False)

    # CASE 5: pending but no longer observed → flip pending=0.
    if existing.in_pending_set and not observed_now:
        await conn.execute(
            "UPDATE gh_review_requested_state"
            " SET in_pending_set = 0, last_observed_at = ?"
            " WHERE repo = ? AND pr_number = ?",
            (now_iso, repo, pr_number),
        )
        return (existing.request_gen, False)

    # CASE 6: dormant and still not observed → no change.
    return (existing.request_gen, False)


async def prune_dormant(
    conn: aiosqlite.Connection,
    *,
    older_than_iso: str,
) -> int:
    """Delete dormant state rows last observed before `older_than_iso`.

    A row is *dormant* when `in_pending_set = 0`. Pending rows are never
    pruned — they represent live review requests and the operator hasn't
    acted on them yet.
    """
    cur = await conn.execute(
        "DELETE FROM gh_review_requested_state WHERE in_pending_set = 0 AND last_observed_at < ?",
        (older_than_iso,),
    )
    deleted = cur.rowcount or 0
    await cur.close()
    return int(deleted)


__all__ = ["StateRow", "get_state", "prune_dormant", "upsert_observation"]
