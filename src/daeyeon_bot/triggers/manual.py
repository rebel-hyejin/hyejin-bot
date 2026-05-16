"""Manual trigger — write-only event source for CLI-driven fires.

There is no live polling loop. Manual events are written directly to the
outbox by `daeyeon-bot dev fire …` (and the per-handler convenience
commands `dev fire-pr-review` / `dev fire-jira-triage`) via the same
`infra.outbox.insert_event` + `enqueue_handler` path the live triggers
use. We register the manifest here so the trigger registry can resolve
the `manual` source name when an outbox row references it.
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
