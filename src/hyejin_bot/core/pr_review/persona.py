"""Backward-compat shim — `Persona` now lives at `hyejin_bot.core.persona`.

This re-export preserves the original import path used by handlers/tests
that landed before feature 002's generalization.
"""

from hyejin_bot.core.persona import Persona

__all__ = ["Persona"]
