"""Outbox poll loop. Claims rows, runs handlers under TaskGroup + Semaphore.

Phase 0: stub.
"""

from __future__ import annotations


async def run_loop() -> None:
    raise NotImplementedError("Phase 1: poll outbox → claim_one → handler.handle → settle")
