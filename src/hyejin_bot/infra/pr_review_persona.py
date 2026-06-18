"""Backward-compat shim — loader lives at `hyejin_bot.infra.persona_loader`.

Feature 002 generalized this module so `jira_triage` can reuse the same
loader. The original `PersonaLoader` API is unchanged; the import path
moved.
"""

from hyejin_bot.infra.persona_loader import PersonaLoader

__all__ = ["PersonaLoader"]
