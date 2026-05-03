"""Domain `Event` — the immutable unit a trigger emits and a handler consumes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CURRENT_EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Event:
    """A trigger-emitted, handler-consumed message persisted in the events table.

    `payload` is intentionally `Mapping[str, Any]`: each event `type` defines its
    own schema, validated at the trigger boundary (zod-equivalent: pydantic).
    The dispatcher itself never inspects payloads.
    """

    id: str
    type: str
    schema_version: int
    payload: Mapping[str, Any]
    trace_id: str
    created_at: datetime


def migrate(event: Event) -> Event:
    """Lazy event-payload migration hook.

    Phase 0 stub: returns the event unchanged. Real migrators land per-type
    when payload schemas evolve. Called by handlers before validation.
    """
    return event
