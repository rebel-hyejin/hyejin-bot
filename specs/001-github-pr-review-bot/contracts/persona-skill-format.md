# Contract — Persona SKILL.md format

The persona is an operator-authored Claude Code skill at
`~/.claude/skills/<name>/SKILL.md`. The bot reads it on every review
request (with mtime-based caching) and uses the markdown body as the
review system prompt.

---

## 1. File location

```
~/.claude/skills/{name}/SKILL.md
```

- `{name}` is the directory's basename, also the value the operator puts in
  `[handlers.pr_review].persona_skill = "{name}"` in `config.toml`.
- Multiple variants live as sibling directories: e.g.
  `~/.claude/skills/pr-review/`, `~/.claude/skills/pr-review-strict/`,
  `~/.claude/skills/pr-review-korean/`.
- Switching between variants is a config edit + reload — no code change.

The bot resolves `~` against the daemon's HOME at boot. Symlinked
directories work; the bot stats the symlink target for mtime.

---

## 2. File format

```markdown
---
name: pr-review
description: Daeyeon's GitHub PR review persona.
allowed-tools:
  - Read
  - Grep
---

# PR Review Persona

You are reviewing a GitHub pull request on Daeyeon's behalf...

## What to focus on
- Correctness first...
- Then maintainability...

## What NOT to do
- Don't nitpick whitespace...
```

The file MAY have a YAML frontmatter (lines between two `---` delimiters at
the very top of the file). The frontmatter is parsed-but-ignored at runtime
(FR-005) — the bot does **not** key any behavior off `name`, `description`,
`allowed-tools`, or any other frontmatter field. The frontmatter exists so
the same file is also a valid Claude Code skill the operator can run
interactively in their IDE.

The body — everything after the frontmatter (or the entire file if no
frontmatter) — is the review system prompt verbatim. Markdown structure
inside the body (`#`, `##`, lists, code fences) is preserved.

---

## 3. Validation rules (FR-007)

The loader (`infra/pr_review_persona.py:load_active_persona`) MUST validate:

| Check | Reason | Failure → |
|---|---|---|
| File exists at `~/.claude/skills/{name}/SKILL.md` | persona configured but missing | `DeadLetter("persona unavailable: SKILL.md not found at <path>")` |
| File readable | file mode prevents read | `DeadLetter("persona unavailable: cannot read SKILL.md (<errno>)")` |
| Body length ≥ `min_persona_chars` (default 200, configurable) after frontmatter strip | empty / accidentally-cleared persona | `DeadLetter("persona unavailable: body too short (<n> chars, min <min>)")` |
| Body has at least one non-whitespace line | placeholder content | `DeadLetter("persona unavailable: body is whitespace-only")` |

Any failure returns a `DeadLetter` from the handler — no generic fallback
review is ever generated. Operator must repair the persona and `hyejin-bot
ops replay --confirm` to resume.

---

## 4. Hot-reload semantics (FR-006)

```python
# Pseudocode
class PersonaLoader:
    _cache: Persona | None = None

    def load(self, *, name: str) -> Persona:
        path = home() / ".claude" / "skills" / name / "SKILL.md"
        st = path.stat()                     # raises FileNotFoundError → DeadLetter
        if self._cache and self._cache.skill_dir == path.parent \
           and self._cache.mtime_ns == st.st_mtime_ns:
            return self._cache
        body = _strip_frontmatter(path.read_text(encoding="utf-8"))
        _validate_body(body)                 # raises ValidationError → DeadLetter
        persona = Persona(skill_dir=path.parent, name=name, body=body, mtime_ns=st.st_mtime_ns)
        self._cache = persona
        return persona
```

- Stat-on-each-review is acceptable; an mtime-ignoring cache is forbidden.
- The cache is daemon-lifetime (reset on restart and on persona-name change
  via `lifecycle reload-config`).
- `_strip_frontmatter` removes the leading `---\n…\n---\n` block when the
  file starts with `---\n`; otherwise it returns the file as-is. Frontmatter
  parse errors are ignored (it's parsed-but-ignored).

---

## 5. What the persona body should and should NOT contain

### SHOULD

- Tone, focus areas, severity bar, language preference (Korean/English).
- House conventions (e.g., "always flag missing tests", "Python: prefer
  pathlib over os.path").
- Examples of comments the operator considers good or bad.

### SHOULD NOT

- Operator's secrets, internal hostnames, customer names — these end up in
  every Claude prompt and risk leaking via Claude's logs.
- Prompt-injection-style tricks like "ignore previous instructions" — they
  conflict with the appended schema instructions in `claude-review-output.md`
  and may produce malformed JSON (which then `DeadLetter`s).
- Direct "always APPROVE" or "always REQUEST_CHANGES" instructions — the
  bot ignores them; review is hard-coded to `event=COMMENT`.

The bot does NOT enforce these "should not" items in code (it can't reliably
distinguish secrets from ordinary words in arbitrary persona prose). The
contract is documentary — operator's responsibility, with the redaction
processor as the safety net for output.

---

## 6. Example minimal valid SKILL.md

```markdown
---
name: pr-review
description: Default PR review persona for hyejin-bot.
---

You are reviewing GitHub pull requests as Daeyeon. Your job is to leave
useful, specific feedback that helps the author ship a better change.

## Priorities (in order)

1. Correctness — bugs, race conditions, error-handling gaps.
2. Maintainability — naming, structure, file size, comments.
3. Tests — meaningful coverage of new behavior; flag missing tests.
4. Conventions — match the surrounding codebase's style.

## Voice

- Direct and specific. Quote the code line you're commenting on.
- Skip nitpicks (whitespace, blank lines, single-character names that are
  conventional like `i`, `n`, `e`).
- If the change is fine as-is, the Summary should say so plainly.

## Don't

- Don't suggest sweeping rewrites unrelated to the PR.
- Don't echo or guess at secrets you see in the diff; flag them
  abstractly.
```

This file is 200+ chars after frontmatter strip and has multiple non-blank
lines, so it passes the FR-007 sanity check.
