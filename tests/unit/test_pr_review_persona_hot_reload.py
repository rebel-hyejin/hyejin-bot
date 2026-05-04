"""Hot-reload contract for `infra.pr_review_persona.PersonaLoader` (T037).

The handler calls `loader.load()` on every event, so the operator's
`SKILL.md` edits must be picked up without a daemon restart. The loader
caches by `mtime_ns` — a touched file invalidates the cache and the next
`load()` returns the new body.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from daeyeon_bot.infra.pr_review_persona import PersonaLoader
from tests.fakes.pr_persona import materialize_persona

_BODY_A = (
    "You are reviewing pull requests as a kind reviewer who always praises "
    "the code and looks for things to celebrate."
)
_BODY_B = (
    "You are reviewing pull requests as a strict reviewer who always flags "
    "missing tests and proposes one concrete additional test case."
)


def test_persona_edit_takes_effect_on_next_load(tmp_path: Path) -> None:
    """Two consecutive `load()` calls separated by an mtime bump return
    different bodies — no PersonaLoader rebuild, no daemon restart.
    """
    skill_path = materialize_persona(tmp_path, "pr-reviewer", body=_BODY_A)
    loader = PersonaLoader(skills_root=tmp_path)

    first = loader.load("pr-reviewer", min_chars=50)
    assert _BODY_A in first.body
    assert _BODY_B not in first.body
    n1 = first.mtime_ns

    time.sleep(0.02)
    skill_path.write_text("---\nname: pr-reviewer\n---\n\n" + _BODY_B, encoding="utf-8")
    new_mtime = time.time_ns()
    os.utime(skill_path, ns=(new_mtime, new_mtime))

    second = loader.load("pr-reviewer", min_chars=50)
    assert _BODY_B in second.body
    assert _BODY_A not in second.body
    assert second.mtime_ns != n1
    assert second is not first
