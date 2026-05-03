"""Atomic token-bucket rate limiter persisted in SQLite. Phase 0 stub."""

from __future__ import annotations


async def take(bucket: str) -> bool:
    raise NotImplementedError(
        "Phase 1: atomic UPDATE buckets SET tokens = tokens - 1 WHERE name=? AND tokens >= 1"
    )
