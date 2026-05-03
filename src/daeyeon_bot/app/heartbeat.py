"""Heartbeat task: 30s file touch + sd_notify (systemd). Phase 0 stub."""

from __future__ import annotations


async def run() -> None:
    raise NotImplementedError("Phase 3: 30s tick — touch file + sd_notify(WATCHDOG=1)")
