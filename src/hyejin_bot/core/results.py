"""HandlerResult sum type. See `CONTRACTS.md` §2."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ack:
    """Successful handling. Outbox row → 'acked'; dedup key written with TTL."""


@dataclass(frozen=True, slots=True)
class Retry:
    """Transient failure. Dispatcher reschedules at `now + after_s`."""

    after_s: float


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """Permanent failure. Operator must `ops replay` to resume."""

    reason: str


HandlerResult = Ack | Retry | DeadLetter
