"""Outbox adapter — claim-row pattern for at-least-once delivery.

Phase 0: stub.
"""

from __future__ import annotations


async def insert() -> None:
    raise NotImplementedError("Phase 1: INSERT INTO outbox (event_id, handler, status='pending')")


async def claim_one() -> None:
    raise NotImplementedError(
        "Phase 1: UPDATE outbox SET claimed_by=? WHERE id=? AND claimed_by IS NULL"
    )


async def settle() -> None:
    raise NotImplementedError("Phase 1: status transition + dedup_keys insert + runs row")
