"""Heartbeat staleness + run_until_stopped loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from daeyeon_bot.app import heartbeat


def test_staleness_seconds_returns_none_when_missing(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    assert heartbeat.staleness_seconds(flag, now_ts=time.time()) is None


def test_staleness_seconds_measures_age(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    flag.touch()
    now_ts = flag.stat().st_mtime + 12.0
    age = heartbeat.staleness_seconds(flag, now_ts=now_ts)
    assert age is not None
    assert 11.5 <= age <= 12.5


def test_is_stale_true_when_missing(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    assert heartbeat.is_stale(flag, now_ts=time.time()) is True


def test_is_stale_false_when_fresh(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    flag.touch()
    fresh_ts = flag.stat().st_mtime + 1.0
    assert heartbeat.is_stale(flag, now_ts=fresh_ts, tick_s=10.0) is False


def test_is_stale_true_past_threshold(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    flag.touch()
    stale_ts = flag.stat().st_mtime + 10.0 * heartbeat.STALE_FACTOR + 1.0
    assert heartbeat.is_stale(flag, now_ts=stale_ts, tick_s=10.0) is True


@pytest.mark.asyncio
async def test_run_until_stopped_touches_and_exits(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(
        heartbeat.run_until_stopped(flag, stop, tick_s=0.02),
        stop_soon(),
    )
    assert flag.exists()


@pytest.mark.asyncio
async def test_run_until_stopped_exits_immediately_when_stop_set(tmp_path: Path) -> None:
    flag = tmp_path / "heartbeat"
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(heartbeat.run_until_stopped(flag, stop, tick_s=10.0), timeout=0.5)
    assert flag.exists()


def test_sd_notify_ready_no_op_without_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # No-op path simply returns; nothing to assert except it doesn't raise.
    heartbeat._sd_notify_ready()  # pyright: ignore[reportPrivateUsage]
