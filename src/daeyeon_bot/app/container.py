"""Composition root.

The ONLY place where concrete `infra` adapters and `triggers` / `handlers`
plugins are wired together. Tests build their own container with fakes.

Production wiring is intentionally tiny — just enough to hold the components
the dispatcher and CLI need. Real DI complexity (heartbeat / supervisor /
secrets) lands as later phases come online.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiosqlite

from daeyeon_bot.app.config import Config
from daeyeon_bot.app.registry import HandlerRegistry, build_handler_registry
from daeyeon_bot.core.time import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class Container:
    """Aggregate of wired-up components for one daemon process."""

    config: Config
    clock: Clock
    db: aiosqlite.Connection
    handlers: HandlerRegistry
    claude_session_factory: Callable[[], Any]


def _real_claude_factory() -> Callable[[], Any]:
    """Phase 1 placeholder. Phase 4 returns a real-SDK-backed session."""

    def _factory() -> Any:
        raise NotImplementedError("Phase 4: real claude_agent_sdk session")

    return _factory


@dataclass(frozen=True, slots=True)
class ContainerOverrides:
    """Optional swap-ins for tests (or for `--insecure-env` style debug runs)."""

    clock: Clock | None = None
    claude_session_factory: Callable[[], Any] | None = None


def build(
    config: Config,
    db: aiosqlite.Connection,
    *,
    overrides: ContainerOverrides | None = None,
) -> Container:
    """Wire concrete dependencies. `db` must already be opened + migrated."""
    overrides = overrides or ContainerOverrides()
    return Container(
        config=config,
        clock=overrides.clock or SystemClock(),
        db=db,
        handlers=build_handler_registry(config),
        claude_session_factory=overrides.claude_session_factory or _real_claude_factory(),
    )
