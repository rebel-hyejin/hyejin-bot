"""Boot and shutdown orchestration.

Boot order (DO NOT REORDER — see `docs/PLAN.md` §2.3):
    1. config load
    2. logging init
    3. pidfile + flock
    4. SQLite open + migrations apply
    5. secrets load
    6. permission probe
    7. container build
    8. heartbeat task
    9. dispatcher start
   10. triggers start
   11. wait for SIGTERM / SIGINT

Shutdown is 2-phase with a 180s total budget (`docs/PLAN.md` §2.4):
    Phase A — stop accepting new events
    Phase B — drain in-flight (≤120s); timeouts → status='interrupted'
    Phase C — finalize (≤30s): heartbeat off, WAL checkpoint, lock release

Phase 0: stub. Real implementation lands in Phase 1 (boot) and Phase 2 (shutdown).
"""

from __future__ import annotations


async def boot() -> None:
    raise NotImplementedError("Phase 1: implement boot order from docs/PLAN.md §2.3")


async def shutdown() -> None:
    raise NotImplementedError("Phase 2: implement 2-phase shutdown from docs/PLAN.md §2.4")
