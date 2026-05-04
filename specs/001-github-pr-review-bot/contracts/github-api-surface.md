# Contract — GitHub API surface used by the bot

This file is **the only place** that lists which GitHub endpoints the bot
calls. If a call doesn't appear here, it doesn't happen. FR-010b ("never
modify the PR") is enforced by code review against this enumeration: any
new write endpoint must be added here first.

All calls are made via `gh api` subprocess (see `research.md` R1, R2). All
JSON shapes below show only the fields the bot reads/writes — extra fields
returned by GitHub are ignored.

---

## Endpoints used (5 total — 4 read, 1 write)

### 1. `GET /search/issues` — polling query

```
gh api -X GET /search/issues \
  -f q='is:pr is:open review-requested:@me archived:false' \
  -f per_page=100
```

**Read shape (subset)**:
```jsonc
{
  "items": [
    {
      "number": 42,
      "repository_url": "https://api.github.com/repos/owner/repo",
      "pull_request": {
        "url": "https://api.github.com/repos/owner/repo/pulls/42",
        "draft": false
      }
    }
  ]
}
```

The bot derives `repo = "owner/repo"` from `repository_url` (last two path
segments) and `pr_number = number`. It then calls endpoint #2 to get the
current head SHA and reviewer list (the search response does NOT include those
fields reliably).

### 2. `GET /repos/{owner}/{repo}/pulls/{n}` — PR metadata

```
gh api /repos/{owner}/{repo}/pulls/42
```

**Read shape (subset)**:
```jsonc
{
  "number": 42,
  "title": "...",
  "body": "...",
  "head": { "sha": "abc123..." },
  "user": { "login": "alice" },
  "draft": false,
  "state": "open",
  "requested_reviewers": [ { "login": "daeyeon-lee" } ]
}
```

Used by:
- the trigger to learn the current `head_sha` after spotting the PR in #1
- the handler for PR title/body/author/reviewer-list (self-authored +
  withdrawn checks).

### 3. `GET /repos/{owner}/{repo}/pulls/{n}/files` — changed-files list

```
gh api -X GET /repos/{owner}/{repo}/pulls/42/files \
  --paginate -f per_page=100
```

**Read shape (subset, per page item)**:
```jsonc
{
  "filename": "src/foo.py",
  "status": "modified",
  "additions": 42,
  "deletions": 7,
  "changes": 49,
  "patch": "@@ -10,7 +10,42 @@ ..."
}
```

Used by:
- size-budget check: sum `additions + deletions` and count items.
- diff content for Claude.
- inline-comment validation: `_filter_anchors()` parses `patch` to determine
  which `(path, line)` pairs are valid anchors per FR-012.

### 4. `GET /user` — operator identity probe (boot only)

```
gh api /user
```

**Read shape (subset)**:
```jsonc
{ "login": "daeyeon-lee" }
```

Run once at boot to populate `github.username` if not set in config. Cached
for the daemon lifetime (re-fetched on `daeyeon-bot lifecycle reload-config`).

### 5. `POST /repos/{owner}/{repo}/pulls/{n}/reviews` — post review (ONLY write)

```
gh api -X POST /repos/{owner}/{repo}/pulls/42/reviews --input -
```

**Write body**:
```jsonc
{
  "commit_id": "abc123...",
  "event": "COMMENT",
  "body": "Updated review for SHA abc123 (supersedes earlier bot review posted at 14:30:11 UTC)\n\n## Summary\n...",
  "comments": [
    {
      "path": "src/foo.py",
      "line": 42,
      "side": "RIGHT",
      "body": "Consider extracting this into a helper..."
    },
    {
      "path": "src/bar.py",
      "start_line": 10,
      "line": 15,
      "side": "RIGHT",
      "body": "This loop is O(n²); see comment on line 11."
    }
  ]
}
```

**Read shape on success (subset)**:
```jsonc
{
  "id": 9876543,
  "submitted_at": "2026-05-04T14:31:02Z",
  "state": "COMMENTED",
  "html_url": "https://github.com/owner/repo/pull/42#pullrequestreview-9876543"
}
```

The bot records `id`, `submitted_at`, and `html_url` in `pr_review_audit`.

#### Constants (NOT parameters)

The following are baked in the wrapper, not exposed as caller arguments:
- `event` is **always** `"COMMENT"`. The wrapper rejects anything else.
- The wrapper sends `commit_id` from the snapshot the bot reviewed, not
  whatever the PR's current head is at post-time. This pins the review to
  the SHA actually examined (FR-009).

---

## Endpoints we do NOT call

Any non-trivial write endpoint is forbidden. The following are explicitly
**banned** for the bot per FR-010b. Any addition requires a spec amendment
and a new entry in this file.

| Endpoint | Why banned |
|---|---|
| `PATCH /repos/{o}/{r}/pulls/{n}` | Would modify PR title/body/state. |
| `POST /repos/{o}/{r}/issues/{n}/labels` | PR labels are out of bounds. |
| `POST /repos/{o}/{r}/issues/{n}/assignees` | Assignees are out of bounds. |
| `POST /repos/{o}/{r}/issues/{n}/comments` | We don't post issue comments — Summary goes in the review body. |
| `DELETE /repos/{o}/{r}/pulls/{n}/reviews/{r}` | The API doesn't permit deleting `event=COMMENT` reviews; we use chronological supersede instead (FR-017). |
| `PUT /repos/{o}/{r}/pulls/{n}/reviews/{r}/dismissals` | Same — would dismiss someone's review; out of scope. |
| `POST /repos/{o}/{r}/pulls/{n}/requested_reviewers` | Modifying the reviewer set is out of scope. |
| `POST /repos/{o}/{r}/issues/{n}/reactions` | Reactions are noise. |

---

## Auth & rate-limit error contract

- `gh` returns non-zero exit on HTTP 4xx/5xx. The wrapper inspects stderr:
  - `HTTP 401` or "authentication failed" ⇒ raise `core.errors.AuthError`.
    Dispatcher halts; CLI exits 78. Operator runs `gh auth refresh`.
  - `HTTP 403` with `X-RateLimit-Remaining: 0` ⇒ raise `RateLimitError`.
    Dispatcher schedules `Retry(rate_limit_backoff_s)`; trigger backs off.
  - `HTTP 422` (validation) on `POST /reviews` ⇒ raise `PermanentError`
    (the bot already validates anchors locally, so a 422 means a logic
    bug, not a transient).
  - Other 5xx ⇒ raise `TransientError` ⇒ `Retry(default_backoff_s)`.
  - 404 on `GET pulls/{n}` ⇒ `PermanentError("PR not found or no access")` ⇒
    `DeadLetter`.

- Trigger-side 401/auth-error also halts the daemon (the polling task can't
  silently spin while broken).
