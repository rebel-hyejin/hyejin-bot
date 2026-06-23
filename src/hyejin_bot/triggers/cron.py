"""Generic daily-cron polling trigger (feature 003).

Unlike `gh_review_requested` / `jira_assigned` (which poll an external API
and diff the result against persisted state), this trigger has no external
source: it watches the wall clock and emits a single configured event once
per local-tz calendar day, at or after a scheduled `HH:MM`.

The first user is the `news` handler — `news.daily` fired every morning so
the daemon DMs hyejin the tech-news clip without a separate launchd timer.

Fire-once-per-day:
    Each tick reads `cron_state.last_fired_date` (a YYYY-MM-DD local date).
    The job fires only when:
      * today's local date != last_fired_date  (not already fired today), AND
      * the current local time >= the scheduled HH:MM.
    The state UPSERT and the event INSERT commit in one transaction, so a
    crash between them can neither double-fire nor skip a day. The
    `events.UNIQUE(source, source_dedup_key)` constraint — keyed on the
    job name + local date — is a second guard against a double emit.

Catch-up semantics:
    A daemon that boots after the scheduled time but on a day it hasn't yet
    fired will fire immediately (the date check passes and `now >= HH:MM`).
    A daemon down across the whole window simply misses that day — there is
    no backfill (a stale news clip helps no one), and no miss is logged: the
    trigger is stateless about days it never observed, so "missed" isn't a
    distinguishable event. The next day fires normally.

Errors:
    AuthError      → re-raise (halts the daemon, exit 78) — defensive; this
                     trigger touches no auth surface, but keeps the contract.
    other transient/permanent → log + continue (next tick retries).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
import structlog

from hyejin_bot.core.errors import AuthError, PermanentError, TransientError
from hyejin_bot.core.events import make_event
from hyejin_bot.core.manifest import TriggerManifest
from hyejin_bot.core.protocols import EmitFn, TriggerContext
from hyejin_bot.core.time import Clock
from hyejin_bot.infra import cron_state, outbox

_log = structlog.get_logger(__name__)

_SOURCE = "cron"

MANIFEST = TriggerManifest(
    name="cron",
    source=_SOURCE,
    retryable_at_source=False,
)

StorageFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]
PermanentFailureReporter = Callable[[str], Awaitable[bool]]


def _never_paused() -> bool:
    return False


@dataclass(slots=True)
class CronTrigger:
    """Daily wall-clock trigger that emits one configured event per local day."""

    job_name: str
    event_type: str
    handler_name: str
    # Scheduled local time-of-day. The job fires on the first tick at or after
    # this time on a day it has not already fired.
    schedule_hour: int
    schedule_minute: int
    timezone_name: str
    storage_factory: StorageFactory
    clock: Clock
    # Tick cadence. Should be well under an hour so the job fires close to
    # the scheduled minute; 300s (5 min) matches the other triggers' default.
    poll_interval_seconds: float
    manifest: TriggerManifest = MANIFEST
    pause_check: Callable[[], bool] = _never_paused
    permanent_failure_reporter: PermanentFailureReporter | None = None

    def _tz(self) -> tzinfo:
        return ZoneInfo(self.timezone_name)

    async def run(self, emit: EmitFn, ctx: TriggerContext) -> None:
        """Loop until cancelled. AuthError propagates and halts the daemon."""
        del emit, ctx  # trigger persists events directly via storage_factory.
        while True:
            if self.pause_check():
                _log.info("cron.paused", job=self.job_name)
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            try:
                fired = await self.tick_once()
            except AuthError:
                raise
            except TransientError as exc:
                _log.warning("cron.tick_failed", job=self.job_name, error=str(exc))
            except PermanentError as exc:
                _log.warning("cron.tick_failed", job=self.job_name, error=str(exc))
                if (
                    self.permanent_failure_reporter is not None
                    and await self.permanent_failure_reporter(str(exc))
                ):
                    _log.error("cron.quarantined", job=self.job_name, error=str(exc))
                    return
            else:
                if fired:
                    _log.info("cron.fired", job=self.job_name, event_type=self.event_type)
            await asyncio.sleep(self.poll_interval_seconds)

    async def tick_once(self) -> bool:
        """One wall-clock check. Returns True iff an event was emitted."""
        now_utc = self.clock.now()
        now_local = now_utc.astimezone(self._tz())
        today = now_local.date().isoformat()

        # Has the scheduled minute arrived in local time today?
        due = (now_local.hour, now_local.minute) >= (self.schedule_hour, self.schedule_minute)
        if not due:
            return False

        async with self.storage_factory() as conn:
            if await cron_state.last_fired_date(conn, job_name=self.job_name) == today:
                return False  # already fired today

            now_iso = now_utc.astimezone(UTC).isoformat()
            emitted = await self._emit_event(conn, fired_date=today, now=now_utc, now_iso=now_iso)
            # Mark fired whether we emitted OR the emit was deduped by the
            # `(source, source_dedup_key)` UNIQUE. A dedup hit means another
            # instance (e.g. an overlapping daemon during a deploy) already
            # fired today's job — without recording it, THIS instance would
            # retry the insert on every tick for the rest of the day. Either
            # way, today's job is handled; record it and move on.
            await cron_state.mark_fired(
                conn,
                job_name=self.job_name,
                fired_date=today,
                fired_at_iso=now_iso,
            )
            await conn.commit()
            return emitted

    async def _emit_event(
        self,
        conn: aiosqlite.Connection,
        *,
        fired_date: str,
        now: Any,
        now_iso: str,
    ) -> bool:
        payload: dict[str, Any] = {
            "job": self.job_name,
            "scheduled_date": fired_date,
            "fired_at": now_iso,
        }
        # Dedup on (job, local date): a same-day re-emit is a no-op even if the
        # state row write lost a race. The job name is in the seed so two cron
        # jobs sharing the source can't collide.
        seed = f"cron|{self.job_name}|{fired_date}"
        dedup_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        event = make_event(type=self.event_type, payload=payload, created_at=now)
        inserted = await outbox.insert_event(
            conn, event, source=_SOURCE, source_dedup_key=dedup_key
        )
        if not inserted:
            return False
        await outbox.enqueue_handler(conn, event_id=event.id, handler=self.handler_name, now=now)
        return True


__all__ = [
    "MANIFEST",
    "CronTrigger",
    "PermanentFailureReporter",
    "StorageFactory",
]
