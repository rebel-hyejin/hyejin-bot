"""Composition root.

The ONLY place where concrete `infra` and `triggers` / `handlers` are wired
together. Tests build their own container with fakes. Phase 0: stub.
"""

from __future__ import annotations

from dataclasses import dataclass

from daeyeon_bot.app.config import Config


@dataclass(frozen=True, slots=True)
class Container:
    """Aggregate of every wired-up component. Built once in `lifecycle.boot`."""

    config: Config


def build(config: Config) -> Container:
    """Wire concrete dependencies for production. Phase 0: returns the bare config holder."""
    return Container(config=config)
