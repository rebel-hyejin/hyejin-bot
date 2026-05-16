"""HostResolver — async DNS resolution with per-instance caching."""

from __future__ import annotations

import socket
from collections.abc import Callable

import pytest

from daeyeon_bot.infra.host_resolver import HostResolver


def _patch_gethostbyname(monkeypatch: pytest.MonkeyPatch, fn: Callable[[str], str]) -> list[str]:
    """Replace socket.gethostbyname; return the call-log for assertions."""
    log: list[str] = []

    def _wrapped(name: str) -> str:
        log.append(name)
        return fn(name)

    monkeypatch.setattr(socket, "gethostbyname", _wrapped)
    return log


async def test_resolve_returns_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gethostbyname(monkeypatch, lambda _n: "10.0.0.5")
    resolver = HostResolver()
    assert await resolver.resolve("ssw-giga-02") == "10.0.0.5"


async def test_resolve_caches_within_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call for the same name does NOT re-hit DNS."""
    log = _patch_gethostbyname(monkeypatch, lambda _n: "10.0.0.5")
    resolver = HostResolver()
    assert await resolver.resolve("ssw-giga-02") == "10.0.0.5"
    assert await resolver.resolve("ssw-giga-02") == "10.0.0.5"
    assert log == ["ssw-giga-02"]


async def test_resolve_returns_none_on_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str) -> str:
        raise socket.gaierror(-2, "Name or service not known")

    _patch_gethostbyname(monkeypatch, _boom)
    resolver = HostResolver()
    assert await resolver.resolve("nonexistent-host") is None


async def test_resolve_caches_negative_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed lookup is cached too — we don't retry within one triage."""
    log = _patch_gethostbyname(
        monkeypatch,
        lambda _n: (_ for _ in ()).throw(socket.gaierror(-2, "x")),
    )
    resolver = HostResolver()
    assert await resolver.resolve("bad") is None
    assert await resolver.resolve("bad") is None
    assert log == ["bad"]


async def test_resolve_empty_input_returns_none_without_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _patch_gethostbyname(monkeypatch, lambda _n: "10.0.0.5")
    resolver = HostResolver()
    assert await resolver.resolve("") is None
    assert await resolver.resolve("   ") is None
    assert log == []


async def test_distinct_names_cached_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"a": "1.1.1.1", "b": "2.2.2.2"}
    log = _patch_gethostbyname(monkeypatch, lambda n: answers[n])
    resolver = HostResolver()
    assert await resolver.resolve("a") == "1.1.1.1"
    assert await resolver.resolve("b") == "2.2.2.2"
    assert await resolver.resolve("a") == "1.1.1.1"
    assert log == ["a", "b"]


async def test_resolve_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    log = _patch_gethostbyname(monkeypatch, lambda _n: "10.0.0.5")
    resolver = HostResolver()
    assert await resolver.resolve("  ssw-giga-02  ") == "10.0.0.5"
    assert log == ["ssw-giga-02"]


async def test_resolve_offloads_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the blocking syscall runs in a worker thread, not the event loop."""
    import threading

    main_thread = threading.get_ident()
    seen_threads: list[int] = []

    def _record_thread(name: str) -> str:
        seen_threads.append(threading.get_ident())
        return "10.0.0.5"

    _patch_gethostbyname(monkeypatch, _record_thread)
    resolver = HostResolver()
    await resolver.resolve("ssw-giga-02")
    assert seen_threads == [seen_threads[0]]
    assert seen_threads[0] != main_thread, (
        "DNS lookup ran on the event loop thread — async wrapper not engaged"
    )
