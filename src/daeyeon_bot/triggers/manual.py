"""Manual trigger — fires events from the CLI (`daeyeon-bot dev fire manual`).

Phase 0: defines MANIFEST only. Implementation lands in Phase 1.
"""

from __future__ import annotations

from daeyeon_bot.core.manifest import TriggerManifest
from daeyeon_bot.core.protocols import EmitFn, TriggerContext

MANIFEST = TriggerManifest(
    name="manual",
    source="manual",
    retryable_at_source=False,
)


class ManualTrigger:
    """The 'manual' source has no live trigger loop.

    Manual events are written to the outbox synchronously by `daeyeon-bot dev fire`
    (see `daeyeon_bot.cli.dev`), so there's nothing to do here. We keep the class
    as a placeholder so the manifest is still discoverable in the trigger registry.
    """

    manifest = MANIFEST

    async def run(self, emit: EmitFn, ctx: TriggerContext) -> None:
        return None
