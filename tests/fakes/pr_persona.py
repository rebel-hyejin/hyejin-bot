"""Helper to materialize SKILL.md fixtures for tests."""

from __future__ import annotations

from pathlib import Path


def materialize_persona(
    skills_root: Path,
    name: str,
    body: str,
    *,
    frontmatter: str | None = "name: pr-review\ndescription: test persona.\n",
) -> Path:
    """Write a valid SKILL.md under `skills_root/<name>/` and return the path.

    `frontmatter` is the YAML-block content between the two `---` delimiters.
    Pass `frontmatter=None` to write a body-only file (no frontmatter).
    """
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    if frontmatter is None:
        skill_path.write_text(body, encoding="utf-8")
    else:
        skill_path.write_text(
            f"---\n{frontmatter}---\n\n{body}",
            encoding="utf-8",
        )
    return skill_path


__all__ = ["materialize_persona"]
