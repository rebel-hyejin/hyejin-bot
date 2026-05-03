"""Echo handler — calls Claude with the event payload's `message`, returns Ack.

Phase 0: defines MANIFEST. Body lands in Phase 1 against the fake ClaudeSession.
"""

from __future__ import annotations

from datetime import timedelta

from daeyeon_bot.core.manifest import HandlerManifest

MANIFEST = HandlerManifest(
    name="echo",
    idempotent=True,
    dedup_ttl=timedelta(days=1),
    side_effect_key=None,
    concurrency=1,
    accepts=("manual.message",),
)


class EchoHandler:
    """Phase 1 implementation."""

    manifest = MANIFEST

    async def handle(self, event, ctx):
        raise NotImplementedError("Phase 1: open ClaudeSession and Ack")
