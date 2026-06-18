"""Manual replay: bump attempt_epoch on an outbox row and re-queue it.

The contract (PLAN.md §3.6, CONTRACTS §6):
    1. Operator targets one event_id (and optionally one handler).
    2. Each affected outbox row gets attempt_epoch += 1, status reset to
       'pending', claimed_by/claimed_at/last_error/next_attempt_at cleared,
       attempt counter reset to 0.
    3. A `runs` audit row is inserted with status='replay_requested' and
       triggered_by='manual_replay' so the audit log shows operator intent
       even before the dispatcher picks the row up.
    4. Returns a `ReplayPlan` describing what would change (dry-run) or what
       was changed (commit).

A new attempt_epoch means the dedup_keys row from a prior Ack does NOT match
the next claim — the handler runs again with side_effect_key dedup as the
last line of defense against duplicate external effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import aiosqlite

REPLAY_RUN_STATUS = "replay_requested"
REPLAY_TRIGGER = "manual_replay"


@dataclass(frozen=True, slots=True)
class ReplayTarget:
    outbox_id: int
    handler: str
    current_status: str
    current_attempt_epoch: int


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    """What `replay()` did or would do."""

    event_id: str
    targets: tuple[ReplayTarget, ...]
    committed: bool

    @property
    def empty(self) -> bool:
        return not self.targets


async def plan_replay(
    conn: aiosqlite.Connection, *, event_id: str, handler: str | None = None
) -> tuple[ReplayTarget, ...]:
    """List outbox rows that would be replayed, without modifying anything."""
    if handler is None:
        sql = "SELECT id, handler, status, attempt_epoch FROM outbox WHERE event_id = ?"
        params: tuple[object, ...] = (event_id,)
    else:
        sql = "SELECT id, handler, status, attempt_epoch FROM outbox WHERE event_id = ? AND handler = ?"
        params = (event_id, handler)
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return tuple(
        ReplayTarget(
            outbox_id=int(r["id"]),
            handler=r["handler"],
            current_status=r["status"],
            current_attempt_epoch=int(r["attempt_epoch"]),
        )
        for r in rows
    )


async def replay(
    conn: aiosqlite.Connection,
    *,
    event_id: str,
    handler: str | None = None,
    now: datetime,
) -> ReplayPlan:
    """Bump attempt_epoch and re-queue. Caller already verified `--confirm`.

    Skips rows in 'running' state — that means the dispatcher is mid-handle and
    racing the operator. Operator should `lifecycle pause` first.
    """
    targets = await plan_replay(conn, event_id=event_id, handler=handler)
    replayable = tuple(t for t in targets if t.current_status != "running")
    if not replayable:
        return ReplayPlan(event_id=event_id, targets=(), committed=True)

    iso_now = now.isoformat()
    for target in replayable:
        await conn.execute(
            """
            UPDATE outbox
               SET status = 'pending',
                   attempt_epoch = attempt_epoch + 1,
                   attempt = 0,
                   claimed_by = NULL,
                   claimed_at = NULL,
                   last_error = NULL,
                   next_attempt_at = NULL,
                   updated_at = ?
             WHERE id = ?
            """,
            (iso_now, target.outbox_id),
        )
        await conn.execute(
            """
            INSERT INTO runs(outbox_id, event_id, handler, attempt_epoch, started_at,
                             finished_at, status, duration_ms, triggered_by, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target.outbox_id,
                event_id,
                target.handler,
                target.current_attempt_epoch + 1,
                iso_now,
                iso_now,
                REPLAY_RUN_STATUS,
                0,
                REPLAY_TRIGGER,
                None,
            ),
        )
    await conn.commit()
    return ReplayPlan(event_id=event_id, targets=replayable, committed=True)


__all__ = [
    "REPLAY_RUN_STATUS",
    "REPLAY_TRIGGER",
    "ReplayPlan",
    "ReplayTarget",
    "plan_replay",
    "replay",
]
