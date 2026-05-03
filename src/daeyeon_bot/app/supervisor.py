"""Trigger supervision: backoff + quarantine after 5 fails / 10 min. Phase 0 stub."""

from __future__ import annotations


async def supervise() -> None:
    raise NotImplementedError("Phase 2: transient backoff + quarantine policy")
