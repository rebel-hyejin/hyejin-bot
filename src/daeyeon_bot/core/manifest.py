"""Trigger and Handler manifests. See `CONTRACTS.md` §3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class HandlerManifest:
    name: str
    idempotent: bool
    dedup_ttl: timedelta
    side_effect_key: str | None = None
    concurrency: int = 1
    accepts: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TriggerManifest:
    name: str
    source: str
    retryable_at_source: bool = False
