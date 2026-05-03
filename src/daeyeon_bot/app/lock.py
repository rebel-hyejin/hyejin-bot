"""pidfile + flock(2) for single-instance enforcement. Phase 0 stub."""

from __future__ import annotations


def acquire() -> None:
    raise NotImplementedError("Phase 2: write pidfile, fcntl.flock(LOCK_EX | LOCK_NB)")


def release() -> None:
    raise NotImplementedError("Phase 2: flock release + pidfile unlink")
