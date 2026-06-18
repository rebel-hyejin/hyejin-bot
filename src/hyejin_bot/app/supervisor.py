"""Trigger supervision: sliding-window failure tracker + quarantine.

Policy (PLAN.md §5 Phase 2):
    A trigger that raises PermanentError 5 times within a 10-minute window is
    quarantined. The dispatcher / trigger loop should consult `is_quarantined`
    before invoking the trigger; the operator clears the row via
    `cli inspect triggers --unquarantine <name>` (Phase 3).

In-process state (`FailureWindow`) tracks recent failure timestamps. Persistent
state (`quarantine` table) records the decision so it survives restarts.

Phase 2: data path + tests; the only live trigger is `manual` which doesn't
fail, so the policy is exercised purely in unit tests.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiosqlite

DEFAULT_THRESHOLD = 5
DEFAULT_WINDOW = timedelta(minutes=10)


@dataclass(slots=True)
class FailureWindow:
    """Sliding-window failure counter for one trigger.

    Not thread-safe; assume the trigger task is the only writer. The dispatcher
    loop owns the supervisor, so single-writer holds.
    """

    threshold: int = DEFAULT_THRESHOLD
    window: timedelta = DEFAULT_WINDOW
    _failures: deque[datetime] = field(default_factory=deque[datetime])

    def record_failure(self, at: datetime) -> bool:
        """Append a failure; return True if this trip the trigger past threshold."""
        self._evict(at)
        self._failures.append(at)
        return len(self._failures) >= self.threshold

    def reset(self) -> None:
        self._failures.clear()

    def _evict(self, now: datetime) -> None:
        cutoff = now - self.window
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()


@dataclass(slots=True)
class TriggerSupervisor:
    """Per-trigger failure tracker + persistent quarantine writer."""

    threshold: int = DEFAULT_THRESHOLD
    window: timedelta = DEFAULT_WINDOW
    _windows: dict[str, FailureWindow] = field(default_factory=dict[str, FailureWindow])

    def _get(self, trigger_name: str) -> FailureWindow:
        win = self._windows.get(trigger_name)
        if win is None:
            win = FailureWindow(threshold=self.threshold, window=self.window)
            self._windows[trigger_name] = win
        return win

    async def record_failure(
        self,
        conn: aiosqlite.Connection,
        *,
        trigger_name: str,
        reason: str,
        at: datetime,
    ) -> bool:
        """Record a permanent failure. Persists a quarantine row when the
        threshold is hit. Returns True iff the trigger is now quarantined.
        """
        tripped = self._get(trigger_name).record_failure(at)
        if tripped and not await is_quarantined(conn, trigger_name=trigger_name):
            await conn.execute(
                """
                INSERT OR REPLACE INTO quarantine(trigger_name, quarantined_at, reason)
                VALUES (?, ?, ?)
                """,
                (trigger_name, at.isoformat(), reason),
            )
            await conn.commit()
        return tripped

    def record_success(self, trigger_name: str) -> None:
        """Reset the in-process window after a healthy run."""
        win = self._windows.get(trigger_name)
        if win is not None:
            win.reset()


async def is_quarantined(conn: aiosqlite.Connection, *, trigger_name: str) -> bool:
    async with conn.execute(
        "SELECT 1 FROM quarantine WHERE trigger_name = ? LIMIT 1", (trigger_name,)
    ) as cur:
        row = await cur.fetchone()
    return row is not None


async def list_quarantined(conn: aiosqlite.Connection) -> list[dict[str, str]]:
    async with conn.execute(
        "SELECT trigger_name, quarantined_at, reason FROM quarantine ORDER BY quarantined_at"
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "trigger_name": r["trigger_name"],
            "quarantined_at": r["quarantined_at"],
            "reason": r["reason"],
        }
        for r in rows
    ]


async def unquarantine(conn: aiosqlite.Connection, *, trigger_names: Iterable[str]) -> int:
    names = list(trigger_names)
    if not names:
        return 0
    placeholders = ",".join("?" * len(names))
    cur = await conn.execute(
        f"DELETE FROM quarantine WHERE trigger_name IN ({placeholders})",  # noqa: S608
        names,
    )
    await conn.commit()
    return cur.rowcount


__all__ = [
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW",
    "FailureWindow",
    "TriggerSupervisor",
    "is_quarantined",
    "list_quarantined",
    "unquarantine",
]
