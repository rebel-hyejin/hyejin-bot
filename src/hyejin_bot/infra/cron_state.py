"""Persistent state for the daily-cron trigger (feature 003).

One row per cron job in `cron_state`, tracking the local-tz calendar date the
job last fired. The trigger reads `last_fired_date` to enforce fire-once-per-day
even across daemon restarts, and writes it in the same transaction as the
event INSERT so a crash between the two can't double-fire nor drop a day.
"""

from __future__ import annotations

import aiosqlite


async def last_fired_date(conn: aiosqlite.Connection, *, job_name: str) -> str | None:
    """Return the YYYY-MM-DD local date the job last fired, or None if never."""
    async with conn.execute(
        "SELECT last_fired_date FROM cron_state WHERE job_name = ?",
        (job_name,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return str(row["last_fired_date"])


async def mark_fired(
    conn: aiosqlite.Connection,
    *,
    job_name: str,
    fired_date: str,
    fired_at_iso: str,
) -> None:
    """Record that `job_name` fired on local date `fired_date`.

    Does NOT commit — the caller wraps this in the same transaction as the
    event INSERT so the state UPSERT and the emit are atomic.
    """
    await conn.execute(
        "INSERT INTO cron_state(job_name, last_fired_date, last_fired_at)"
        " VALUES (?, ?, ?)"
        " ON CONFLICT(job_name) DO UPDATE SET"
        " last_fired_date = excluded.last_fired_date,"
        " last_fired_at = excluded.last_fired_at",
        (job_name, fired_date, fired_at_iso),
    )


__all__ = ["last_fired_date", "mark_fired"]
