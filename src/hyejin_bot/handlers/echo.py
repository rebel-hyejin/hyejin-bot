"""Echo handler — calls Claude with `payload.message`, returns Ack.

Phase 1: validates the payload, opens a ClaudeSession from the context's factory,
queries Claude, logs the response. Settle (in dispatcher) records status=acked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import structlog

from hyejin_bot.core.errors import ValidationError
from hyejin_bot.core.events import Event
from hyejin_bot.core.manifest import HandlerManifest
from hyejin_bot.core.protocols import HandlerContext
from hyejin_bot.core.results import Ack, HandlerResult

_log = structlog.get_logger(__name__)

MANIFEST = HandlerManifest(
    name="echo",
    idempotent=True,
    dedup_ttl=timedelta(days=1),
    side_effect_key=None,
    concurrency=1,
    accepts=("manual.message",),
)


@dataclass(slots=True)
class EchoHandler:
    manifest: HandlerManifest

    async def handle(self, event: Event, ctx: HandlerContext) -> HandlerResult:
        message = event.payload.get("message")
        if not isinstance(message, str) or not message:
            raise ValidationError("echo expects payload.message: non-empty str")

        session = ctx.claude_session_factory()
        async with session as s:  # type: ignore[attr-defined]
            response = await s.query(message)  # type: ignore[attr-defined]

        _log.info(
            "echo.acked",
            event_id=event.id,
            trace_id=ctx.trace_id,
            response_preview=response[:80],
        )
        return Ack()
