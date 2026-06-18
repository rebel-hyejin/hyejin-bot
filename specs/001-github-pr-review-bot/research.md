# Phase 0 Research — GitHub PR Review Automation Bot

Resolves every open question raised by the spec's Technical Context and the
project's existing contracts. Each entry follows: **Decision → Rationale →
Alternatives**.

---

## R1. GitHub authentication

**Decision**: Delegate to the operator's local `gh` CLI. The bot calls
`gh auth status --hostname github.com -t` at boot to confirm a token exists, then
uses `gh api ...` for every API call (search, PR fetch, review post). The token
itself never lands in `os.environ`, the daemon's secrets store, or logs.

**Rationale**:
- Spec FR-014 nailed this. `gh` is already installed and authenticated on the
  operator's machine; reusing it eliminates a Keychain entry and a token-rotation
  procedure (operator runs `gh auth refresh` instead).
- `gh` handles auth headers, retry on 401, and the operator's existing 2FA setup.
- The 5000-req/hr REST budget is plenty for one operator's poll cadence (worst
  case ~20 PRs × ~3 calls each per 5 min = ~720 req/hr).

**Alternatives**:
- Keychain-stored PAT (rejected — spec explicitly forbids and adds rotation work).
- GitHub App installation (rejected — would post as the App identity, not the
  operator; also adds infra).
- OAuth device flow inside the daemon (rejected — duplicate of what `gh` does;
  also harder to keep tokens out of `os.environ`).

---

## R2. GitHub API client (Python httpx vs. gh subprocess)

**Decision**: Drive every GitHub call as a subprocess of `gh api`. Capture stdout
JSON via `asyncio.create_subprocess_exec(... , stdout=PIPE)`. POST bodies are
piped into stdin (`-f`/`-F` flags exist but `--input -` is cleaner for the
review-post which has nested arrays).

**Rationale**:
- No new dependency (the daemon stays tight). `httpx` is **not** currently a
  declared dep, and adding it for one feature crosses the size budget.
- `gh` already does retry-on-rate-limit, header parsing, and auth — code we'd
  otherwise re-implement.
- Process spawn cost is negligible at 5-min polling cadence.

**Alternatives**:
- Add `httpx` + handcraft the auth header (rejected — duplicates `gh`).
- `PyGithub` (rejected — heavier dep, sync-only, also duplicates auth).

**Implementation note**: All `gh api` calls go through one async wrapper at
`infra/gh_cli.py` so retries, structured-log redaction, and stderr capture live
in one place. Tests use a `FakeGh` substitute injected via the container.

---

## R3. Polling vs. webhook for `review_requested`

**Decision**: Polling. One trigger task wakes every `poll_interval_seconds`
(default 300) and runs:

```
gh api -X GET /search/issues \
  -f q='is:pr is:open review-requested:@me archived:false'
```

The list of `(repo, PR number, head_sha)` tuples is reconciled against the
`gh_review_requested_state` table to detect:
- new PRs entering the set ⇒ `request_gen=1` event
- head SHA changed while in the set ⇒ `request_gen += 1` event
- PR left and re-entered the set ⇒ `request_gen += 1` event

**Rationale**:
- Webhooks need a public ingress (ngrok, Cloudflare Tunnel, etc.). The daemon
  is single-tenant on the operator's laptop/server; standing up ingress is more
  ops surface than the latency saving (5-min p95 vs. seconds) is worth.
- Spec SC-002 allows up to 10 min auto-detection, well within 5-min poll cadence.
- `search/issues` with `review-requested:@me` is the official GitHub query for
  the operator's pending review queue and is documented to update within seconds
  of the request being made on GitHub's side.

**Alternatives**:
- GitHub Webhook → local HTTP server (rejected — ingress + secret management
  + replay handling, all for sub-minute latency that the spec doesn't need).
- Long-poll via `gh api` (no such endpoint exists for review-requested).

**Edge handling**:
- When `gh api` returns rate-limit/abuse response (HTTP 403 with rate headers),
  trigger backs off using `Retry-After` from the response and parks itself
  (existing supervisor's quarantine kicks in after 5 fails / 10 min).
- The polling trigger NEVER bypasses outbox; every event goes through
  `infra/outbox.py:insert_event` so dedup, recovery, and replay all behave
  identically to manual-fired events.

---

## R4. Re-request semantics & request_gen state machine

**Decision** (confirmed by spec Clarifications): trigger unit is the **request
instance**, not the head SHA. The polling trigger maintains a per-PR row in
`gh_review_requested_state(repo, pr_number, head_sha, request_gen, in_pending_set,
last_observed_at)` and increments `request_gen` whenever:

1. The PR enters the search result set for the first time (`gen` starts at `1`).
2. The PR's `head_sha` changes while still in the set (new push).
3. The PR was previously in the set, left, and now re-entered (author clicked
   "Re-request review"); detected by `in_pending_set` flipping `false → true`.

The trigger then writes:
```
events(
  source           = 'gh_review_requested',
  source_dedup_key = sha256("gh-review-requested|{repo}#{pr}@{sha}#{gen}"),
  type             = 'gh.review_requested',
  payload          = {repo, pr_number, head_sha, request_gen, requested_at}
)
```
and the `outbox` row for `pr_review`. The (state-table UPSERT + events INSERT +
outbox INSERT) all happen in one SQLite transaction so a crash mid-poll cannot
desynchronize state vs. enqueue.

**Rationale**:
- A naive `(repo, pr, head_sha)` dedup misses re-requests at the same SHA, which
  is exactly what spec Clarifications session flagged as a gap.
- `events.UNIQUE(source, source_dedup_key)` is the existing race-safe path; we
  align our generated key with the dedup_token formula in spec FR-018a.

**Alternatives**:
- Use timeline events (`POST /repos/{owner}/{repo}/issues/{n}/events`) to detect
  `review_requested` events directly (rejected — needs per-PR API call per
  poll, ~10× the request budget; also harder to detect "still in queue" vs
  "withdrawn").

---

## R5. Posting reviews: GitHub API mechanics

**Decision**: `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with body:

```json
{
  "commit_id": "<head_sha>",
  "event": "COMMENT",
  "body": "<Summary text>",
  "comments": [
    {"path": "src/foo.py", "line": 42, "side": "RIGHT", "body": "..."},
    {"path": "src/bar.py", "start_line": 10, "line": 15, "side": "RIGHT", "body": "..."}
  ]
}
```

Sent via `gh api -X POST repos/{owner}/{repo}/pulls/{n}/reviews --input -`.
The bot reads the response and stores `review_id`, `submitted_at` in
`pr_review_audit` (Phase 1 data model).

**Rationale**:
- Atomic: Summary + inline comments arrive as one review object, so they appear
  as a single unit in GitHub's UI.
- `event="COMMENT"` (not `APPROVE`/`REQUEST_CHANGES`) matches FR-010a — the bot
  never blocks merges or claims approval authority.
- `commit_id` pins the review to the SHA that was reviewed, so a subsequent
  force-push cannot retroactively change which code the review applies to.

**Alternatives**:
- Issue comment (`/issues/{n}/comments`) for Summary + separate per-file PR
  comments (rejected — they'd appear as N+1 separate notifications, fail the
  "single review object" UX, and lose the SHA pinning).
- Submit Summary first, then inline comments on a pending review (rejected —
  two API calls, higher failure surface for an atomic outcome).

**Inline-comment anchor edge case** (FR-012): If Claude flags `src/foo.py:42`
but the diff at the head SHA has only `src/foo.py:10..30`, GitHub returns 422
on that comment. Strategy: validate every inline comment against the diff
hunks BEFORE posting; comments whose anchor falls outside any hunk get folded
into the Summary as a bullet (`- [src/foo.py near L42] body`) and removed
from the `comments` array. This keeps the post atomic and never silently
drops feedback.

---

## R6. Persona loading and hot-reload

**Decision**: Read `~/.claude/skills/<name>/SKILL.md` on every review request
(stat the file, compare `mtime_ns` against last-loaded value, re-read on
change). Strip optional YAML frontmatter (everything between the first two
`---` lines, when the file starts with `---`). Use the remaining markdown
body verbatim as the system prompt to the Claude session.

**Rationale**:
- Operator can edit SKILL.md and the next review reflects it (FR-006), no
  daemon restart required.
- Stat-on-each-review is O(1); even at 100 reviews/day the kernel stat cache
  swallows it. A long-lived in-memory cache that ignores mtime is explicitly
  forbidden by FR-006.
- Frontmatter is parsed-but-ignored at runtime (FR-005). Storing the full
  parsed frontmatter would invite the bot to depend on it; ignoring keeps the
  same SKILL.md usable in Claude Code IDE without divergent semantics.

**Sanity validation** (FR-007):
- Body MUST be ≥ `min_persona_chars` (config knob, default 200) after
  frontmatter strip.
- Body MUST contain at least one non-whitespace line.
- File MUST be readable. Any failure ⇒ `DeadLetter("persona unavailable: <reason>")`.

**Alternatives**:
- File-watch (inotify/FSEvents) instead of stat-per-review (rejected —
  cross-platform glue is heavier than a stat, and we already pay one syscall
  to open the file anyway).
- TOML/JSON persona instead of skill format (rejected — operator already uses
  Claude Code skills; reusing the same format means the same persona works
  interactively in IDE).

---

## R7. Claude output structure & validation

**Decision**: Prompt Claude to return a single JSON object matching this Pydantic v2 schema:

```python
class InlineComment(BaseModel):
    path: str
    line: int = Field(ge=1)
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    start_line: int | None = None
    body: str = Field(min_length=1, max_length=8000)

class ReviewOutput(BaseModel):
    summary: str = Field(min_length=1, max_length=8000)
    comments: list[InlineComment] = Field(default_factory=list, max_length=200)
```

System prompt = persona body + a fixed "Output ONLY a JSON object matching this
schema: ..." appendix. Model: existing `claude.model` (default `claude-opus-4-7`).
After Claude returns, parse and validate with Pydantic. On parse/validate
failure: one retry with the validation error appended to the prompt; second
failure ⇒ `DeadLetter("claude returned malformed review")`.

**Rationale**:
- Pydantic v2 is already a project dep — no new lib.
- Hard limits (`max_length`, `max_length=200` for comments) bound the GitHub
  request size; avoids the 65k-char body limit on review submissions.
- Two-attempt retry pattern follows the daemon's `Retry → DeadLetter` ladder
  rather than re-prompting indefinitely.

**Alternatives**:
- Structured-output via SDK tool-use (rejected for now — `claude-agent-sdk`'s
  structured output API is still evolving; sticking with prompt-and-validate
  keeps the contract explicit and testable without SDK version lock).
- Free-form output + post-hoc parsing (rejected — too lossy; spec FR-010
  REQUIRES inline comments to be inline, not bullets, so we need structured
  output not heuristics).

---

## R8. Size budget enforcement

**Decision**: Before calling Claude, fetch
`GET /repos/{owner}/{repo}/pulls/{n}/files?per_page=100` (paginated by `gh`).
Sum `additions + deletions` across all pages and count files. Compare against:

- `pr_review.size_budget.lines = 1000` (config-overridable)
- `pr_review.size_budget.files = 50` (config-overridable)

If either threshold exceeded, skip Claude entirely and post the "PR too large
for automated review" Summary with zero inline comments. `event=COMMENT`,
empty `comments` array. Record this as `Ack` (the work is done — the requester
got a clear answer).

**Rationale**:
- Spec FR-013 makes the thresholds operator-configurable with these defaults.
- Counting before calling Claude saves the LLM round-trip (and tokens) for
  obviously-too-big PRs.
- Files endpoint already returns line stats per file, so no separate `gh pr
  diff` is needed for the size check (the diff is only fetched if size passes).

**Alternatives**:
- Truncate the diff and review the prefix (rejected — spec says "never
  partial / truncated review").
- Offer split suggestions (out of scope for v1).

---

## R9. Force re-review (manual override at same SHA)

**Decision**: CLI command `hyejin-bot dev fire pr-review --pr <url|owner/repo#n>
[--force]`. Without `--force`, the handler short-circuits with `Ack` and a log
line `pr_review.skip_already_reviewed` if `pr_review_audit` has a row for that
`(repo, pr, head_sha, request_gen)`. With `--force`, the handler increments a
manual `request_gen` counter (sentinel: `manual_<unix_ts>`) so the dedup_token
becomes unique, then posts a new review whose Summary's first line reads:

```
Updated review for SHA <sha> (supersedes earlier bot review posted at HH:MM:SS UTC)
```

The audit row is updated (latest review_id pointed at the new one) but the
prior `review_id` is appended to a `superseded_review_ids` JSON array column,
preserving history per FR-017.

**Rationale**:
- GitHub's API does not allow deleting/dismissing a review submitted with
  `event=COMMENT`. Chronological supersede is the only honest UX.
- Marking the supersede in the Summary first line ensures the operator and
  the PR author see "this is the latest" without needing to read commit-time
  metadata.

**Alternatives**:
- Post an issue-comment that says "see latest review" (rejected — clutters
  the PR conversation and still doesn't dismiss the prior review).

---

## R10. Self-authored PR & request-withdrawn handling

**Decision**:
- **Self-authored**: Handler fetches `pull_request.user.login` and compares
  against the operator's `github.username` config (resolved via `gh api user`
  at boot, cached for the daemon lifetime). On match ⇒ `Ack` with log
  `pr_review.skip_self_authored`, no review posted, audit row written
  with `status='skipped_self_authored'`.
- **Withdrawn**: Before posting, handler re-fetches the PR's
  `requested_reviewers[].login`. If operator no longer in that list ⇒ `Ack`
  with log `pr_review.skip_withdrawn`, no review posted, audit row written
  with `status='skipped_withdrawn'`.

**Rationale**:
- Both checks happen inside the handler, after the trigger has emitted the
  event. They DO NOT live in the trigger because between trigger and handler
  the state may have changed (queued for several minutes, paused-and-resumed,
  etc.).
- These are explicit `Ack` results, not `DeadLetter` — the request was
  legitimately processed; the answer is "no review needed".

**Alternatives**:
- Filter at trigger time (rejected — racy; the polling result reflects an
  earlier snapshot).

---

## R11. Logging, redaction, and metrics

**Decision**: Use the existing structlog setup. Every handler log includes
`event_id`, `trace_id`, `repo`, `pr_number`, `head_sha`, `request_gen`,
`status`. Diff content and Claude prompts/responses are NEVER logged in full —
only counts (file count, line count, summary char count). Persona body is
NEVER logged.

The existing redaction processor (`infra/logging.py`) already scrubs Slack/
GitHub PAT/Anthropic OAuth patterns plus a high-entropy fallback. That handles
accidental secret leakage from a diff into a log line.

No external metrics export in v1. Operators inspect via
`hyejin-bot inspect status` + the new `pr_review_audit` table queryable
through `hyejin-bot inspect pr-review` (a thin new sub-command).

**Rationale**:
- Aligns with FR-015 (no secrets/paths in posted content) and the daemon's
  existing privacy-by-default posture.

**Alternatives**:
- Prometheus exporter (rejected — out of scope per `docs/PLAN.md` Non-Goals).

---

## R12. Testing strategy

**Decision**:

- **Unit** tests use `FakeGh` (a dict-backed substitute for `infra/gh_cli.py`
  exposing `search`, `pr_files`, `pr_diff`, `pr_get`, `post_review`,
  `auth_status`, `auth_user`) and the existing `FakeClaudeSession` and
  `FakeClock`.
- **Integration** tests mount real `aiosqlite` against `tmp_path`, real
  migrations including `002`, real outbox/dispatcher, and `FakeGh` +
  `FakeClaudeSession`. They exercise:
  - `gh_review_requested` trigger writes an event and outbox row in one tx.
  - Re-request at same SHA flips `in_pending_set` and produces `gen=2`.
  - `pr_review` handler posts via `FakeGh.post_review` and writes audit row.
  - Force-supersede produces a new review and updates audit history.
  - Size-budget overflow posts the "too large" Summary.
- No live GitHub API hit in CI. A separate `just test-live` recipe (manual,
  off by default) runs against a real test PR for smoke validation.

**Coverage targets** (from `docs/PLAN.md` §6.3):
- new `core/pr_review/` and `app/registry.py` additions ≥ 90%
- new `infra/pr_review_*.py` ≥ 80%
- new `cli/dev.py` additions ≥ 60%

**Alternatives**:
- VCR-style fixtures of real `gh api` output (rejected for now — they invite
  drift; the `FakeGh` is small enough to maintain and matches the contract
  documented in `contracts/github-api-surface.md`).

---

## Summary of unresolved items

None. Every Technical Context placeholder was either resolved by spec
Clarifications or by one of R1–R12 above. Phase 0 gate passes.
