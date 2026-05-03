"""Boot and shutdown orchestration.

Boot order (DO NOT REORDER — see `docs/PLAN.md` §2.3):
    1. config load
    2. logging init
    3. pidfile + flock        ← Phase 2
    4. SQLite open + migrate
    5. secrets load            ← Phase 4
    6. permission probe        ← Phase 4
    7. container build
    8. heartbeat task          ← Phase 3
    9. dispatcher start
   10. triggers start          ← Phase 1: manual is fired via CLI; no live triggers
   11. wait for SIGTERM / SIGINT

Phase 1: implements steps 1, 2, 4, 7, 9, 11. Other steps are stubbed in
respective modules and will be wired in their owning phases.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from daeyeon_bot.app.config import load
from daeyeon_bot.app.container import Container, ContainerOverrides, build
from daeyeon_bot.app.dispatcher import Dispatcher
from daeyeon_bot.infra import logging as bot_logging
from daeyeon_bot.infra import storage

_log = structlog.get_logger(__name__)


@dataclass(slots=True)
class BootOptions:
    config_path: str | None = None
    overrides: ContainerOverrides | None = None
    install_signal_handlers: bool = True


async def boot(options: BootOptions | None = None) -> None:
    """Phase 1 boot. Runs until SIGTERM/SIGINT, returns cleanly afterwards."""
    options = options or BootOptions()

    # 1. config
    config = load(options.config_path)
    # 2. logging
    bot_logging.init(level=config.logging.level, fmt=config.logging.format)
    _log.info("boot.start", state_dir=str(config.state_dir_path))

    # 3. pidfile + flock — Phase 2.
    # 4. storage
    config.state_dir_path.mkdir(parents=True, exist_ok=True)
    db = await storage.open_db(config.db_path)
    try:
        await storage.apply_migrations(db)

        # 5. secrets — Phase 4.
        # 6. permissions — Phase 4.
        # 7. container
        container = build(config, db, overrides=options.overrides)
        # 8. heartbeat — Phase 3.

        # 9. dispatcher
        dispatcher = Dispatcher(
            db=container.db,
            handlers=container.handlers,
            claude_session_factory=container.claude_session_factory,
            clock=container.clock,
        )

        # 11. signal-driven shutdown
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        cleanup = (
            _install_signal_handlers(loop, stop_event) if options.install_signal_handlers else None
        )

        async def watch_signals() -> None:
            await stop_event.wait()
            _log.info("boot.shutdown_requested")
            dispatcher.stop()

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(dispatcher.run())
                tg.create_task(watch_signals())
        finally:
            if cleanup is not None:
                cleanup()
            _log.info("boot.exit")
    finally:
        await db.close()


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event
) -> Callable[[], None]:
    """Wire SIGTERM/SIGINT to set `stop_event`. Returns a cleanup callable."""
    handled: list[int] = []

    def _set() -> None:
        if not stop_event.is_set():
            stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _set)
            handled.append(sig)
        except NotImplementedError:  # pragma: no cover — Windows / restricted envs
            continue

    def _cleanup() -> None:
        for sig in handled:
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.remove_signal_handler(sig)

    return _cleanup


async def shutdown() -> None:
    """Phase 2 entry point — full 2-phase shutdown with 180s budget."""
    raise NotImplementedError("Phase 2: implement 2-phase shutdown from docs/PLAN.md §2.4")


__all__ = ["BootOptions", "Container", "boot", "shutdown"]
