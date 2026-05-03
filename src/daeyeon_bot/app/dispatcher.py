"""Outbox poll loop. Claims rows, runs handlers under per-handler Semaphore.

Phase 1: happy path (Ack / Retry / DeadLetter). Errors are mapped to results
according to the contract; classification of TransientError vs PermanentError
goes through `core.errors`.

Phase 2 will add: interrupted-on-restart marking, supervisor backoff, quarantine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

import aiosqlite
import structlog

from daeyeon_bot.app.registry import HandlerRecord, HandlerRegistry
from daeyeon_bot.core.errors import (
    AuthError,
    PermanentError,
    RateLimitError,
    TransientError,
)
from daeyeon_bot.core.events import Event
from daeyeon_bot.core.results import Ack, DeadLetter, HandlerResult, Retry
from daeyeon_bot.core.time import Clock, SystemClock
from daeyeon_bot.infra import outbox

_log = structlog.get_logger(__name__)

DEFAULT_BACKOFF_S = 30.0
RATE_LIMIT_BACKOFF_S = 60.0


@dataclass(slots=True)
class _HandlerCtx:
    """Concrete HandlerContext built per dispatch."""

    clock: Clock
    trace_id: str
    claude_session_factory: object


@dataclass(slots=True)
class Dispatcher:
    """Polls outbox and dispatches claimed rows to handlers."""

    db: aiosqlite.Connection
    handlers: HandlerRegistry
    claude_session_factory: object
    clock: Clock = field(default_factory=SystemClock)
    poll_interval_s: float = 0.5
    claim_id: str = "dispatcher-1"
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _semaphores: dict[str, asyncio.Semaphore] = field(
        default_factory=dict[str, asyncio.Semaphore]
    )

    async def run(self) -> None:
        """Block until `stop()` is called. Drains in-flight tasks before returning."""
        async with asyncio.TaskGroup() as tg:
            while not self._stop.is_set():
                job = await outbox.claim_one(
                    self.db, claimed_by=self.claim_id, now=self.clock.now()
                )
                if job is None:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
                    except TimeoutError:
                        pass
                    continue

                handler_record = self.handlers.by_name.get(job.handler)
                if handler_record is None:
                    _log.warning(
                        "dispatcher.handler_missing",
                        outbox_id=job.outbox_id,
                        handler=job.handler,
                    )
                    started = self.clock.now()
                    await outbox.settle(
                        self.db,
                        job=job,
                        result=DeadLetter(reason=f"handler not registered: {job.handler}"),
                        started_at=started,
                        finished_at=started,
                        dedup_ttl=None,
                    )
                    continue

                semaphore = self._semaphores.setdefault(
                    job.handler, asyncio.Semaphore(handler_record.manifest.concurrency)
                )
                tg.create_task(self._run_one(job, handler_record, semaphore))

    def stop(self) -> None:
        self._stop.set()

    async def _run_one(
        self, job: outbox.ClaimedJob, record: HandlerRecord, semaphore: asyncio.Semaphore
    ) -> None:
        async with semaphore:
            started = self.clock.now()
            result: HandlerResult
            try:
                raw_result = await record.instance.handle(  # type: ignore[attr-defined]
                    job.event,
                    _HandlerCtx(
                        clock=self.clock,
                        trace_id=job.event.trace_id,
                        claude_session_factory=self.claude_session_factory,
                    ),
                )
                result = cast("HandlerResult", raw_result)
            except AuthError:
                # Auth errors halt the daemon. Stop the loop; lifecycle reports exit 78
                # in Phase 2. The current row stays 'running' until the next boot
                # marks it 'interrupted' (Phase 2). Logged loud so it's visible.
                _log.error("dispatcher.auth_error", outbox_id=job.outbox_id)
                self._stop.set()
                return
            except RateLimitError as exc:
                result = self._classify_transient(exc, RATE_LIMIT_BACKOFF_S)
            except TransientError as exc:
                result = self._classify_transient(exc, DEFAULT_BACKOFF_S)
            except (PermanentError, Exception) as exc:
                result = DeadLetter(reason=f"{type(exc).__name__}: {exc}")
            finished = self.clock.now()

            dedup_ttl: timedelta | None = None
            if isinstance(result, Ack) and record.manifest.idempotent:
                dedup_ttl = record.manifest.dedup_ttl

            await outbox.settle(
                self.db,
                job=job,
                result=result,
                started_at=started,
                finished_at=finished,
                dedup_ttl=dedup_ttl,
            )
            _log.info(
                "dispatcher.settled",
                outbox_id=job.outbox_id,
                handler=job.handler,
                event_id=job.event.id,
                status=_result_to_status(result),
            )

    @staticmethod
    def _classify_transient(exc: Exception, default_backoff: float) -> Retry:
        # Future: read RateLimitError.retry_after if the SDK exposes it.
        return Retry(after_s=default_backoff)


def _result_to_status(result: HandlerResult) -> str:
    match result:
        case Ack():
            return "acked"
        case Retry():
            return "retry"
        case DeadLetter():
            return "dead_letter"


# Re-exported types so callers don't reach into infra.outbox.
__all__ = ["DEFAULT_BACKOFF_S", "RATE_LIMIT_BACKOFF_S", "Dispatcher", "Event"]
