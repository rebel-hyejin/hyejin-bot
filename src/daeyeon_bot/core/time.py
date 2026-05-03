"""Clock abstraction. The whole codebase MUST go through `Clock`, not `datetime.now()`.

Why: deterministic tests. `FakeClock` lets us jump time without sleeping.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    """Real wall + monotonic clock. The only `Clock` impl in the production graph."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic(self) -> float:
        return time.monotonic()
