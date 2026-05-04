"""Retention pruning.

PLAN.md §4.2 retention defaults:
    events_days = 90                # delete events older than this …
                                    #   … whose outbox rows are all settled.
    runs_days = 30                  # delete runs older than this …
    runs_keep_per_handler = 10      #   … unless they're in the most-recent N per handler.
    dedup_default_ttl_days = 7      # dedup_keys cleanup honours each row's expires_at.
    gh_state_dormant_days = 90      # delete dormant gh_review_requested_state rows.

Events pruning cascades the outbox rows that reference them — but only if
none of those rows are still active (`pending` / `running` / `retry` /
`interrupted`). An event that's still mid-flight stays put.

`prune()` is idempotent and returns counts so the CLI can report what shrank.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

from daeyeon_bot.app.config import Config
from daeyeon_bot.infra import pr_review_state


@dataclass(frozen=True, slots=True)
class PruneReport:
    runs_deleted: int
    dedup_keys_deleted: int
    events_deleted: int
    outbox_deleted: int
    gh_state_deleted: int = 0


async def prune(conn: aiosqlite.Connection, *, config: Config, now: datetime) -> PruneReport:
    """Apply retention. Caller owns the connection lifecycle."""
    runs_deleted = await _prune_runs(
        conn,
        cutoff=now - timedelta(days=config.retention.runs_days),
        keep_per_handler=config.retention.runs_keep_per_handler,
    )
    dedup_deleted = await _prune_dedup_keys(conn, now=now)
    outbox_deleted, events_deleted = await _prune_events(
        conn,
        cutoff=now - timedelta(days=config.retention.events_days),
    )
    gh_state_deleted = await pr_review_state.prune_dormant(
        conn,
        older_than_iso=(now - timedelta(days=config.retention.gh_state_dormant_days)).isoformat(),
    )
    await conn.commit()
    return PruneReport(
        runs_deleted=runs_deleted,
        dedup_keys_deleted=dedup_deleted,
        events_deleted=events_deleted,
        outbox_deleted=outbox_deleted,
        gh_state_deleted=gh_state_deleted,
    )


async def _prune_runs(
    conn: aiosqlite.Connection, *, cutoff: datetime, keep_per_handler: int
) -> int:
    """Delete runs older than cutoff EXCEPT the latest N per handler.

    Implementation: a window function picks the row-rank inside each handler.
    Anything past keep_per_handler AND older than cutoff goes.
    """
    iso_cutoff = cutoff.isoformat()
    cursor = await conn.execute(
        """
        DELETE FROM runs
         WHERE id IN (
           SELECT id FROM (
             SELECT id, started_at,
                    ROW_NUMBER() OVER (PARTITION BY handler ORDER BY id DESC) AS rk
               FROM runs
           )
           WHERE rk > ? AND started_at < ?
         )
        """,
        (keep_per_handler, iso_cutoff),
    )
    return cursor.rowcount


async def _prune_dedup_keys(conn: aiosqlite.Connection, *, now: datetime) -> int:
    cursor = await conn.execute("DELETE FROM dedup_keys WHERE expires_at < ?", (now.isoformat(),))
    return cursor.rowcount


_DELETE_OUTBOX_FOR_OLD_SETTLED_EVENTS = """
    DELETE FROM outbox
     WHERE event_id IN (
       SELECT id FROM events
        WHERE created_at < ?
          AND id NOT IN (
            SELECT event_id FROM outbox
             WHERE status IN ('pending','running','retry','interrupted')
          )
     )
"""

_DELETE_OLD_EVENTS_WITHOUT_OUTBOX = """
    DELETE FROM events
     WHERE created_at < ?
       AND id NOT IN (SELECT event_id FROM outbox)
"""


async def _prune_events(conn: aiosqlite.Connection, *, cutoff: datetime) -> tuple[int, int]:
    """Drop old events whose outbox rows are all settled. Returns (outbox, events)."""
    iso_cutoff = cutoff.isoformat()
    outbox_cursor = await conn.execute(_DELETE_OUTBOX_FOR_OLD_SETTLED_EVENTS, (iso_cutoff,))
    outbox_deleted = outbox_cursor.rowcount
    events_cursor = await conn.execute(_DELETE_OLD_EVENTS_WITHOUT_OUTBOX, (iso_cutoff,))
    events_deleted = events_cursor.rowcount
    return outbox_deleted, events_deleted


__all__ = ["PruneReport", "prune"]
