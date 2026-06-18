"""CI lint for the repo-bundled `hyejin-bot-jira-triage` persona (T051).

Guards against drift — the bundled SKILL.md must remain a valid persona:
loadable by `PersonaLoader`, body >= min_persona_chars, and contain the
4 canonical section keywords so a refactor doesn't accidentally remove
the output-contract guidance.
"""

from __future__ import annotations

from pathlib import Path

from hyejin_bot.infra.persona_loader import PersonaLoader

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_ROOT = _PROJECT_ROOT / ".claude" / "skills"
_SKILL_NAME = "hyejin-bot-jira-triage"


def test_bundled_skill_file_exists() -> None:
    assert (_BUNDLED_ROOT / _SKILL_NAME / "SKILL.md").exists()


def test_bundled_skill_loads_via_persona_loader() -> None:
    loader = PersonaLoader(skills_root=_BUNDLED_ROOT)
    persona = loader.load(_SKILL_NAME, min_chars=200)
    assert persona.name == _SKILL_NAME
    assert len(persona.body) >= 200


def test_bundled_skill_contains_section_keywords() -> None:
    """Future drift: persona body must mention each TriageOutput field name."""
    body = (_BUNDLED_ROOT / _SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
    lower = body.lower()
    for keyword in ("symptom", "evidence", "layer_rationale", "next_data"):
        assert keyword in lower, f"bundled persona missing keyword: {keyword!r}"


def test_bundled_skill_mentions_six_domain_enum_values() -> None:
    body = (_BUNDLED_ROOT / _SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
    for domain in ("Driver", "SysFw", "CpFw", "SysSol", "DevOps", "Connectivity"):
        assert domain in body, f"bundled persona missing domain ENUM: {domain!r}"


def test_bundled_skill_mentions_stage1_and_stage2() -> None:
    """The Stage 1 / Stage 2 split is part of the contract; PR-4 enables Stage 2."""
    body = (_BUNDLED_ROOT / _SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
    assert "Stage 1" in body
    assert "Stage 2" in body
