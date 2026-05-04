# Contract — Claude review output format

The bot uses Claude as a structured-output review engine. The contract here
is **the format Claude must emit** so the handler can validate it with
Pydantic and forward it to GitHub's review API.

---

## 1. The Pydantic v2 schema (single source of truth)

```python
from typing import Literal
from pydantic import BaseModel, Field

class InlineComment(BaseModel):
    model_config = {"extra": "forbid"}

    path: str = Field(min_length=1, max_length=512)
    line: int = Field(ge=1)
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    start_line: int | None = Field(default=None, ge=1)
    body: str = Field(min_length=1, max_length=8000)

class ReviewOutput(BaseModel):
    model_config = {"extra": "forbid"}

    summary: str = Field(min_length=1, max_length=8000)
    comments: list[InlineComment] = Field(default_factory=list, max_length=200)
```

**`extra = "forbid"`** means any unrecognized key from Claude is a hard
validation failure. This is intentional: a hallucinated key like
`"approve": true` must NOT silently pass into a future API call.

---

## 2. The system prompt assembled by the handler

```text
{persona_body}

---

You are reviewing the pull request below. Output ONLY a JSON object that
matches this exact JSON schema. No prose before or after, no Markdown code
fence — just the JSON object on stdout. If you have nothing to flag, emit
an empty `comments` array but still produce a meaningful `summary`.

JSON schema:
{
  "type": "object",
  "additionalProperties": false,
  "required": ["summary", "comments"],
  "properties": {
    "summary": {
      "type": "string", "minLength": 1, "maxLength": 8000,
      "description": "Top-level review summary. Must mention the head commit SHA you reviewed (it is given to you below)."
    },
    "comments": {
      "type": "array", "maxItems": 200,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["path", "line", "body"],
        "properties": {
          "path":       { "type": "string", "minLength": 1, "maxLength": 512 },
          "line":       { "type": "integer", "minimum": 1 },
          "side":       { "type": "string", "enum": ["RIGHT", "LEFT"] },
          "start_line": { "type": "integer", "minimum": 1 },
          "body":       { "type": "string", "minLength": 1, "maxLength": 8000 }
        }
      }
    }
  }
}

Rules:
- `path` MUST be a path that appears in the changed-files list below.
- `line` MUST refer to a line that exists in that file's diff hunk.
  If you reference a line outside any hunk, the bot will move that
  feedback into the Summary as a bullet rather than post it inline.
- `side` is "RIGHT" for the post-change file (the version after this PR);
  use "LEFT" only when commenting on a line that the PR removed.
- `start_line` is optional. Use it only for multi-line comments
  (start_line < line). For a single-line comment, omit it.
- Never echo content that looks like a secret (API keys, tokens, passwords,
  private keys). If you spot one in the diff, mention it abstractly in the
  Summary; do not paste the value.
```

The user message passed to Claude is the rendered PR snapshot:

```text
Repository: {repo}
PR #{pr_number}: {title}
Author: @{author_login}
Head commit SHA: {head_sha}

PR description:
---
{body}
---

Changed files ({n_files}, +{additions} / -{deletions} lines):

### {path1}  (status: {status1}, +{add1}/-{del1})
```diff
{patch1}
```

### {path2}  ...
```

Binary files and files without a `patch` field are listed by name only with a
`(binary or oversized — diff omitted)` annotation; the persona is told it
cannot review them inline.

---

## 3. Validation pipeline (`handlers/pr_review.py`)

```
claude_response_text
   │
   ▼
JSON parse  ─── fail ──► retry once with parse-error appended ─── fail again ──► DeadLetter
   │
   ▼
Pydantic ReviewOutput.model_validate  ─── fail ──► retry once with errors() appended ─── fail again ──► DeadLetter
   │
   ▼
_filter_anchors(comments, files)
   │  ├─ valid_anchor?  keep in comments[]
   │  └─ invalid_anchor → append "- [{path} near L{line}] {body}" to summary
   ▼
_redact(summary, comments)        # reuse infra/logging.py redaction regexes
   │
   ▼
build POST body  →  gh_cli.post_review(...)
   │
   ▼
on success:  audit row INSERT (status='posted'); Ack
on 422 from API:  raise PermanentError → DeadLetter (logic bug, see anchor filter)
on 5xx / rate-limit:  Retry(...)
```

`_filter_anchors` parses the unified diff hunk headers (`@@ -X,Y +A,B @@`)
to determine which `(path, line)` pairs land in a hunk. If `start_line` is
set, the entire range `[start_line, line]` must lie in a single hunk.

`_redact` runs the structlog redaction regex set
(`infra/logging.py:_REDACTION_PATTERNS`) over both the summary and each
comment body. Any match raises `PermanentError("redaction would alter
posted content")` rather than silently mutating Claude's output — operator
can then audit the diff to find what tripped the check. (This is stricter
than the log-only redaction so SC-008 stays at 100% pass.)

---

## 4. Force-supersede header

When the handler is processing a manual `--force` event whose audit history
shows a prior posted review at the same `(repo, pr, head_sha)`, it prepends
this exact line (and a blank line) to whatever Claude returned:

```
Updated review for SHA <head_sha> (supersedes earlier bot review posted at <HH:MM:SS UTC>)
```

`<HH:MM:SS UTC>` is `submitted_at` of the prior review formatted in UTC.
The character budget for `summary` (8000 chars) accommodates this header.

---

## 5. "Too large" Summary template (no Claude call)

When the size budget is exceeded:

```
ReviewOutput(
  summary=(
    f"This PR is too large for an automated review at SHA `{head_sha}`.\n"
    f"\n"
    f"- Changed files: {n_files} (limit {max_files})\n"
    f"- Changed lines: {n_lines} (limit {max_lines})\n"
    f"\n"
    f"Consider splitting the change into smaller PRs and re-requesting review."
  ),
  comments=[],
)
```

This bypasses Claude entirely and is posted via the same review-API path.
Audit row gets `status='skipped_too_large'`.
