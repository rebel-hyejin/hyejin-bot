"""Hostname → IP resolver with per-instance caching.

Resolves SSW test-host names (`ssw-giga-02`) to IPs for callers that
need an IP literal rather than a name. The Loki query path doesn't use
this anymore (after the 2026-05-16 fwlog/smclog label fix the cluster
keys off `hostname` (name) for every stream), but the resolved IP is
still surfaced in the Run Snapshot's `Run meta` section for context.

One resolver instance is created per triage and discarded after. The
in-process cache survives a single handler call but not across triages
— if a host is re-imaged between triages, the next one re-resolves.

DNS failures are non-fatal: `resolve(name)` returns `None`, and the
caller is expected to record that and continue. Lookup runs in a worker
thread via `asyncio.to_thread` so a slow / timing-out DNS resolver
doesn't block the dispatcher event loop.
"""

from __future__ import annotations

import asyncio
import socket
import threading


class HostResolver:
    """`socket.gethostbyname` with a per-instance dict cache.

    The public `resolve()` is async — it offloads the blocking
    `socket.gethostbyname` call to a worker thread so the dispatcher
    event loop stays responsive even when DNS is slow.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()

    async def resolve(self, name: str) -> str | None:
        """Return the IPv4 string for `name`, or None on DNS failure.

        Empty/whitespace input returns None without touching DNS.
        """
        key = (name or "").strip()
        if not key:
            return None
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        ip = await asyncio.to_thread(self._lookup_blocking, key)
        with self._lock:
            self._cache[key] = ip
        return ip

    @staticmethod
    def _lookup_blocking(key: str) -> str | None:
        try:
            return socket.gethostbyname(key)
        except OSError:
            # gaierror / herror / etc. — all map to "DNS failed".
            return None


__all__ = ["HostResolver"]
