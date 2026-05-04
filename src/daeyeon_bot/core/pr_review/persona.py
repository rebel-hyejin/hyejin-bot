"""Persona dataclass — review system prompt loaded from a Claude Code skill."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Persona:
    """One loaded SKILL.md persona.

    `body` is the markdown after frontmatter strip; the loader uses it verbatim
    as the Claude system prompt. `mtime_ns` is the file's mtime at load time;
    the loader compares it on every call to decide whether to re-read.
    """

    skill_dir: Path
    name: str
    body: str
    mtime_ns: int

    def is_stale(self, *, current_mtime_ns: int) -> bool:
        return current_mtime_ns != self.mtime_ns
