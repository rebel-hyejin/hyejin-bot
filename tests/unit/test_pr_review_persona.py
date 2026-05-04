"""Unit tests for `infra.pr_review_persona.PersonaLoader` (T016)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from daeyeon_bot.core.errors import ValidationError
from daeyeon_bot.infra.pr_review_persona import PersonaLoader
from tests.fakes.pr_persona import materialize_persona

_LONG_BODY = (
    "You are reviewing GitHub pull requests as Daeyeon. Your job is to leave "
    "useful, specific feedback that helps the author ship a better change. "
    "Priorities: correctness, maintainability, tests, conventions."
)


def test_missing_file_raises_validation_error(tmp_path: Path) -> None:
    loader = PersonaLoader(skills_root=tmp_path)
    with pytest.raises(ValidationError) as exc:
        loader.load("does-not-exist", min_chars=10)
    assert "SKILL.md not found" in str(exc.value)


def test_body_too_short_raises_validation_error(tmp_path: Path) -> None:
    materialize_persona(tmp_path, "pr-review", body="short")
    loader = PersonaLoader(skills_root=tmp_path)
    with pytest.raises(ValidationError) as exc:
        loader.load("pr-review", min_chars=200)
    assert "body too short" in str(exc.value)


def test_whitespace_only_body_raises_validation_error(tmp_path: Path) -> None:
    materialize_persona(tmp_path, "pr-review", body=" " * 300)
    loader = PersonaLoader(skills_root=tmp_path)
    with pytest.raises(ValidationError) as exc:
        loader.load("pr-review", min_chars=200)
    assert "whitespace-only" in str(exc.value)


def test_frontmatter_stripped(tmp_path: Path) -> None:
    materialize_persona(
        tmp_path,
        "pr-review",
        body=_LONG_BODY,
        frontmatter="name: pr-review\ndescription: x.\n",
    )
    loader = PersonaLoader(skills_root=tmp_path)
    persona = loader.load("pr-review", min_chars=50)
    assert "name: pr-review" not in persona.body
    assert _LONG_BODY in persona.body
    assert persona.name == "pr-review"


def test_no_frontmatter_returned_verbatim(tmp_path: Path) -> None:
    materialize_persona(tmp_path, "pr-review", body=_LONG_BODY, frontmatter=None)
    loader = PersonaLoader(skills_root=tmp_path)
    persona = loader.load("pr-review", min_chars=50)
    assert persona.body == _LONG_BODY


def test_cache_hit_when_mtime_unchanged(tmp_path: Path) -> None:
    materialize_persona(tmp_path, "pr-review", body=_LONG_BODY)
    loader = PersonaLoader(skills_root=tmp_path)
    a = loader.load("pr-review", min_chars=50)
    b = loader.load("pr-review", min_chars=50)
    assert a is b
    assert a.mtime_ns == b.mtime_ns


def test_cache_miss_when_mtime_changes(tmp_path: Path) -> None:
    skill_path = materialize_persona(tmp_path, "pr-review", body=_LONG_BODY)
    loader = PersonaLoader(skills_root=tmp_path)
    first = loader.load("pr-review", min_chars=50)

    # Bump mtime to a different ns value (sleep tiny bit to guarantee filesystem ticks).
    time.sleep(0.02)
    new_body = _LONG_BODY + "\n\nUpdated guidance: always flag missing tests."
    skill_path.write_text("---\nname: pr-review\n---\n\n" + new_body, encoding="utf-8")
    new_mtime = time.time_ns()
    os.utime(skill_path, ns=(new_mtime, new_mtime))

    second = loader.load("pr-review", min_chars=50)
    assert second is not first
    assert second.mtime_ns != first.mtime_ns
    assert "always flag missing tests" in second.body


def test_empty_persona_name_raises(tmp_path: Path) -> None:
    loader = PersonaLoader(skills_root=tmp_path)
    with pytest.raises(ValidationError):
        loader.load("", min_chars=10)
