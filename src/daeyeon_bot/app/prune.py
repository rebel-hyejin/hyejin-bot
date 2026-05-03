"""Retention pruning.

PLAN.md §4.2 retention defaults:
    runs_days = 30                  # delete runs older than this …
    runs_keep_per_handler = 10      #   … unless they're in the most-recent N per handler.
    dedup_default_ttl_days = 7      # dedup_keys cleanup honours each row's expires_at.

Events / outbox pruning is deliberately deferred — events can still be subjects
of `replay`, and the FK from outbox makes a safe cascade non-trivial. Phase 6
revisits when the audit window has settled.

`prune()` is idempotent and returns counts so the CLI can report what shrank.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

from daeyeon_bot.app.config import Config


@dataclass(frozen=True, slots=True)
class PruneReport:
    runs_deleted: int
    dedup_keys_deleted: int


async def prune(conn: aiosqlite.Connection, *, config: Config, now: datetime) -> PruneReport:
    """Apply retention. Caller owns the connection lifecycle."""
    runs_deleted = await _prune_runs(
        conn,
        cutoff=now - timedelta(days=config.retention.runs_days),
        keep_per_handler=config.retention.runs_keep_per_handler,
    )
    dedup_deleted = await _prune_dedup_keys(conn, now=now)
    await conn.commit()
    return PruneReport(runs_deleted=runs_deleted, dedup_keys_deleted=dedup_deleted)


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


__all__ = ["PruneReport", "prune"]
