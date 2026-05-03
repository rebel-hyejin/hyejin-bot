"""Manual trigger — fires events from the CLI (`daeyeon-bot dev fire manual`).

Phase 0: defines MANIFEST only. Implementation lands in Phase 1.
"""

from __future__ import annotations

from daeyeon_bot.core.manifest import TriggerManifest

MANIFEST = TriggerManifest(
    name="manual",
    source="manual",
    retryable_at_source=False,
)


class ManualTrigger:
    """Phase 1: read events queued via `dev fire`, emit to outbox."""

    manifest = MANIFEST

    async def run(self, emit, ctx) -> None:
        raise NotImplementedError("Phase 1: read incoming queue and emit Event")
