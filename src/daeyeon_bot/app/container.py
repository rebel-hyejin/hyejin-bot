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

import aiosqlite

from daeyeon_bot.app.config import Config
from daeyeon_bot.app.registry import HandlerRegistry, build_handler_registry
from daeyeon_bot.core.time import Clock, SystemClock
from daeyeon_bot.infra.claude import ClaudeSession, make_real_factory


@dataclass(frozen=True, slots=True)
class Container:
    """Aggregate of wired-up components for one daemon process."""

    config: Config
    clock: Clock
    db: aiosqlite.Connection
    handlers: HandlerRegistry
    claude_session_factory: Callable[[], ClaudeSession]


@dataclass(frozen=True, slots=True)
class ContainerOverrides:
    """Optional swap-ins for tests (or for `--insecure-env` style debug runs)."""

    clock: Clock | None = None
    claude_session_factory: Callable[[], ClaudeSession] | None = None


def build(
    config: Config,
    db: aiosqlite.Connection,
    *,
    oauth_token: str | None = None,
    overrides: ContainerOverrides | None = None,
) -> Container:
    """Wire concrete dependencies. `db` must already be opened + migrated."""
    overrides = overrides or ContainerOverrides()
    factory = overrides.claude_session_factory or _build_real_factory(config, oauth_token)
    return Container(
        config=config,
        clock=overrides.clock or SystemClock(),
        db=db,
        handlers=build_handler_registry(config),
        claude_session_factory=factory,
    )


def _build_real_factory(config: Config, oauth_token: str | None) -> Callable[[], ClaudeSession]:
    if oauth_token is None:
        raise RuntimeError(
            "container.build: oauth_token required when no claude_session_factory override"
        )
    return make_real_factory(
        oauth_token=oauth_token,
        model=config.claude.model,
        default_system_prompt=config.claude.default_system_prompt,
    )
