"""Read-only query helpers for `cli inspect` and `ops doctor`.

These never mutate state. Mutating outbox/runs lives in `infra/outbox.py`,
quarantine in `app/supervisor.py`. Keeping reads here means the CLI can lean
on focused helpers without re-deriving SQL across files.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import aiosqlite

from hyejin_bot.core.events import Event

OUTBOX_STATUSES = ("pending", "running", "acked", "retry", "dead_letter", "interrupted")


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: str
    type: str
    source: str
    source_dedup_key: str
    trace_id: str
    created_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OutboxRow:
    id: int
    event_id: str
    handler: str
    status: str
    attempt: int
    attempt_epoch: int
    last_error: str | None
    next_attempt_at: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class RunRow:
    id: int
    outbox_id: int
    event_id: str
    handler: str
    attempt_epoch: int
    started_at: str
    finished_at: str | None
    status: str
    duration_ms: int | None
    triggered_by: str
    error: str | None


async def outbox_status_counts(conn: aiosqlite.Connection) -> dict[str, int]:
    """Count outbox rows grouped by status. Returns 0 for absent statuses."""
    counts = cast("dict[str, int]", dict.fromkeys(OUTBOX_STATUSES, 0))
    async with conn.execute("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status") as cur:
        async for row in cur:
            counts[row["status"]] = int(row["n"])
    return counts


async def list_runs(conn: aiosqlite.Connection, *, limit: int = 20) -> list[RunRow]:
    """Latest `limit` runs ordered by finished_at DESC, fallback id DESC."""
    async with conn.execute(
        """
        SELECT id, outbox_id, event_id, handler, attempt_epoch, started_at, finished_at,
               status, duration_ms, triggered_by, error
          FROM runs
         ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
         LIMIT ?
        """,
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]


async def list_events(conn: aiosqlite.Connection, *, limit: int = 20) -> list[EventRecord]:
    """Latest `limit` events ordered by created_at DESC."""
    async with conn.execute(
        """
        SELECT id, type, source, source_dedup_key, payload_json, trace_id, created_at
          FROM events
         ORDER BY created_at DESC, id DESC
         LIMIT ?
        """,
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_event(r) for r in rows]


async def get_event(conn: aiosqlite.Connection, *, event_id: str) -> EventRecord | None:
    async with conn.execute(
        """
        SELECT id, type, source, source_dedup_key, payload_json, trace_id, created_at
          FROM events WHERE id = ?
        """,
        (event_id,),
    ) as cur:
        row = await cur.fetchone()
    return _row_to_event(row) if row is not None else None


async def outbox_for_event(conn: aiosqlite.Connection, *, event_id: str) -> list[OutboxRow]:
    async with conn.execute(
        """
        SELECT id, event_id, handler, status, attempt, attempt_epoch, last_error,
               next_attempt_at, updated_at
          FROM outbox
         WHERE event_id = ?
         ORDER BY id
        """,
        (event_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_outbox(r) for r in rows]


async def runs_for_event(conn: aiosqlite.Connection, *, event_id: str) -> list[RunRow]:
    async with conn.execute(
        """
        SELECT id, outbox_id, event_id, handler, attempt_epoch, started_at, finished_at,
               status, duration_ms, triggered_by, error
          FROM runs
         WHERE event_id = ?
         ORDER BY id
        """,
        (event_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]


def event_to_dict(event: Event) -> dict[str, Any]:
    """Convert a core Event to a JSON-friendly dict (payload is already mapping)."""
    return {
        "id": event.id,
        "type": event.type,
        "schema_version": event.schema_version,
        "trace_id": event.trace_id,
        "created_at": event.created_at.isoformat(),
        "payload": dict(event.payload),
    }


def _row_to_event(row: aiosqlite.Row) -> EventRecord:
    return EventRecord(
        id=row["id"],
        type=row["type"],
        source=row["source"],
        source_dedup_key=row["source_dedup_key"],
        trace_id=row["trace_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        payload=json.loads(row["payload_json"]),
    )


def _row_to_outbox(row: aiosqlite.Row) -> OutboxRow:
    return OutboxRow(
        id=int(row["id"]),
        event_id=row["event_id"],
        handler=row["handler"],
        status=row["status"],
        attempt=int(row["attempt"]),
        attempt_epoch=int(row["attempt_epoch"]),
        last_error=row["last_error"],
        next_attempt_at=row["next_attempt_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run(row: aiosqlite.Row) -> RunRow:
    return RunRow(
        id=int(row["id"]),
        outbox_id=int(row["outbox_id"]),
        event_id=row["event_id"],
        handler=row["handler"],
        attempt_epoch=int(row["attempt_epoch"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        duration_ms=int(row["duration_ms"]) if row["duration_ms"] is not None else None,
        triggered_by=row["triggered_by"],
        error=row["error"],
    )


__all__ = [
    "OUTBOX_STATUSES",
    "EventRecord",
    "OutboxRow",
    "RunRow",
    "event_to_dict",
    "get_event",
    "list_events",
    "list_runs",
    "outbox_for_event",
    "outbox_status_counts",
    "runs_for_event",
]
