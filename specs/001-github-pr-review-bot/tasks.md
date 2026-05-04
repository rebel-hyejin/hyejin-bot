---
description: "Task list for GitHub PR Review Automation Bot (feature 001)"
---

# Tasks: GitHub PR Review Automation Bot

**Branch**: `001-github-pr-review-bot`
**Input**: design documents in `/specs/001-github-pr-review-bot/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Included. The daemon project enforces strict pyright + pytest with coverage targets (`docs/PLAN.md` §6.3); the spec leans on the existing daemon's at-least-once / dead-letter / replay contracts that are already test-covered. Every new module gets unit tests, and the cross-cutting flow gets one integration test.

**Organization**: Phases run in order; tasks WITHIN a phase marked `[P]` can run in parallel (different files, no shared in-progress dependency). User stories from `spec.md` map to Phase 3 (US1/P1), Phase 4 (US2/P2), Phase 5 (US3/P2), Phase 6 (US4/P3).

## Format: `[ID] [P?] [Story?] Description with file path`

---

## Phase 1: Setup (shared infrastructure)

**Purpose**: Project-level wiring that every later phase depends on. None of these touch business logic — they prepare scaffolding.

- [X] T001 Add the `[github]`, `[triggers.gh_review_requested]`, `[handlers.pr_review]`, `[handlers.pr_review.size_budget]`, retention `gh_state_dormant_days`, and routing entries to `config.example.toml` exactly as specified in `data-model.md` §7. Do NOT enable in your local `config.toml` yet.
- [X] T002 [P] Create empty package `src/daeyeon_bot/core/pr_review/__init__.py` (re-exports added in T011/T012).
- [X] T003 [P] Create migration file `src/daeyeon_bot/infra/db/migrations/002_gh_review_requested_state.sql` with the DDL from `data-model.md` §1 (two `CREATE TABLE IF NOT EXISTS`, two indices, the `UPDATE meta SET value='2'`). Verify `just migrate` applies it cleanly against a fresh `tmp_path` DB.
- [X] T004 [P] Update `tests/unit/test_smoke.py` (or add `tests/unit/test_migration_002.py` if cleaner) to assert `meta.schema_version == '2'` and that both new tables exist after `apply_pending_migrations`.

**Checkpoint**: Migration is idempotent and DB schema is at version 2. No source code beyond the SQL file has changed yet.

---

## Phase 2: Foundational (blocking prerequisites for ALL user stories)

**Purpose**: Domain types, the GitHub-CLI wrapper, the persona loader, the audit/state CRUD adapters, and config models. Every user story in Phase 3+ imports from these.

⚠️ **CRITICAL**: No user-story phase can start until this phase is complete.

### Core domain types (stdlib-only, no I/O)

- [ ] T005 [P] Create `src/daeyeon_bot/core/pr_review/types.py` with the dataclasses from `data-model.md` §3: `PullRequestRef`, `ChangedFile`, `PullRequestSnapshot`, `InlineCommentDraft`, `ReviewDraft`, `PostedReview`. All `frozen=True, slots=True`. Stdlib only.
- [ ] T006 [P] Create `src/daeyeon_bot/core/pr_review/persona.py` with the `Persona` dataclass + `is_stale(current_mtime_ns)` method per `data-model.md` §3.
- [ ] T007 [P] Create `src/daeyeon_bot/core/pr_review/audit.py` with the `AuditRow` dataclass per `data-model.md` §3.
- [ ] T008 [P] Update `src/daeyeon_bot/core/pr_review/__init__.py` to re-export the public types from T005–T007.

### Config model extensions

- [ ] T009 In `src/daeyeon_bot/app/config.py`: add `GitHubConfig` (username, gh_call_timeout_seconds), `GhReviewRequestedTriggerEntry` (enabled, poll_interval_seconds), `SizeBudget` (max_lines=1000, max_files=50), `PrReviewHandlerEntry` (extends `HandlerEntry` with persona_skill, min_persona_chars=200, size_budget). Wire `Config.github`, `Config.triggers.gh_review_requested`, `Config.handlers.pr_review` so `[github]` / `[triggers.gh_review_requested]` / `[handlers.pr_review]` parse correctly. Keep existing fields untouched.
- [ ] T010 [P] Add `tests/unit/test_config.py` cases (extending the existing file) verifying: `[github]` parses with empty `username`, `[triggers.gh_review_requested].poll_interval_seconds=300` is the default, `[handlers.pr_review.size_budget]` defaults to (1000, 50), and env override `DAEYEON_BOT__HANDLERS__PR_REVIEW__PERSONA_SKILL=other` works.

### `gh` CLI wrapper

- [ ] T011 Create `src/daeyeon_bot/infra/gh_cli.py` exposing an async `GhCli` class with methods: `auth_status()`, `auth_user() -> str`, `search_review_requested(username: str) -> list[dict]` (uses query `is:open is:pr review-requested:{username} archived:false` against `GET /search/issues`, paginated via `--paginate`), `pr_get(repo, pr_number) -> dict`, `pr_files(repo, pr_number) -> list[dict]` (paginated via `--paginate`), `post_review(repo, pr_number, *, commit_id, body, comments) -> dict`. All methods use `asyncio.create_subprocess_exec("gh", "api", ...)` with stdout-pipe + JSON parse. Error mapping from `contracts/github-api-surface.md` §"Auth & rate-limit error contract": HTTP 401 → `core.errors.AuthError`; HTTP 403 + rate headers → `RateLimitError`; HTTP 422 on POST → `PermanentError`; other 5xx → `TransientError`; HTTP 404 on GET → `PermanentError("PR not found or no access")`. No retries inside the wrapper; the dispatcher handles them.
- [ ] T012 [P] Create `tests/fakes/gh_cli.py` with a `FakeGh` class implementing the same async interface as `GhCli` but backed by an in-memory dict of canned responses. Include helpers `add_pr(repo, pr, *, head_sha, author, requested, files=…, body=…)` and `posted_reviews()` for assertions.
- [ ] T013 [P] Add `tests/unit/test_gh_cli.py` covering the wrapper's error mapping using a `FakeProcess` (or monkeypatched `asyncio.create_subprocess_exec`). At minimum: 401 → AuthError, 403+rate-limit → RateLimitError, 422 → PermanentError, 5xx → TransientError, success path returns parsed JSON.

### Persona loader (mtime-cached)

- [ ] T014 Create `src/daeyeon_bot/infra/pr_review_persona.py` exposing `PersonaLoader` per `contracts/persona-skill-format.md` §4: `load(name: str, *, min_chars: int) -> Persona`. Stat the file each call; reuse cache when `mtime_ns` matches. `_strip_frontmatter` removes a leading `---\n…\n---\n` block. `_validate_body` enforces `len(body) >= min_chars` and ≥1 non-whitespace line. Failures raise `core.errors.ValidationError("persona unavailable: <reason>")`.
- [ ] T015 [P] Create `tests/fakes/pr_persona.py` with `materialize_persona(tmp_path, name, body, frontmatter=…)` helper that writes a valid SKILL.md and returns the path.
- [ ] T016 [P] Add `tests/unit/test_pr_review_persona.py`. Cover: missing file → ValidationError, body too short → ValidationError, frontmatter stripped correctly (with and without frontmatter), mtime unchanged → cache hit (same Persona id), mtime changed → re-read produces new Persona with new body.

### Audit + state CRUD

- [ ] T017 Create `src/daeyeon_bot/infra/pr_review_audit.py` with async functions: `insert_audit(conn, *, event_id, repo, pr_number, head_sha, request_gen, status, review_id=None, submitted_at=None, summary_chars=None, inline_comment_count=None, persona_skill=None, persona_mtime_ns=None, error=None)`, `find_latest(conn, repo, pr_number, head_sha) -> AuditRow | None`, `record_supersede(conn, audit_id, *, new_review_id, new_submitted_at)` (UPDATE that appends old `review_id` to `superseded_review_ids` JSON array). All operations single-statement.
- [ ] T018 [P] Add `tests/unit/test_pr_review_audit.py` against a real `aiosqlite` in `tmp_path`. Cover: insert posted row → find_latest returns it; insert skipped_self_authored row → find_latest still returns it; supersede appends old review_id and updates new one; CHECK constraint rejects unknown status.
- [ ] T019 Create `src/daeyeon_bot/infra/pr_review_state.py` with async functions: `upsert_observation(conn, *, repo, pr_number, observed_now: bool, head_sha: str | None, now_iso: str) -> tuple[int, bool]` returning `(request_gen, should_emit)` per the state machine in `data-model.md` §5 (cases 1–6). All branches in one transaction. Plus `get_state(conn, repo, pr_number) -> StateRow | None` and `prune_dormant(conn, *, older_than_iso: str) -> int`.
- [ ] T020 [P] Add `tests/unit/test_pr_review_state.py` with one case per state-machine branch (1–6 from `data-model.md` §5). Verify `request_gen` increments correctly and `should_emit` matches the spec.

**Checkpoint**: Foundation ready — `gh_cli`, persona, audit, state, config, and migration land. From this point each user story is implementable in isolation.

---

## Phase 3: User Story 1 — Manual review of a specific PR (Priority: P1) 🎯 MVP

**Story goal**: Operator runs `daeyeon-bot dev fire pr-review --pr <url|owner/repo#n> [--force]` and within ~30 s a persona-driven review (Summary + inline comments) appears on the PR. Self-authored / withdrawn / too-large PRs short-circuit with the right audit status. Force re-review at the same SHA appends supersede header + preserves prior review_id in audit history.

**Independent test (from spec.md)**: With a real test PR you own (small, ≥1 obvious issue), run `dev fire pr-review --pr ...`. Verify on GitHub: one review object, Summary identifies the head SHA, ≥1 inline comment anchored to a flagged line. `daeyeon-bot inspect pr-review --pr ...` shows audit row `status=posted`.

### Pydantic schemas + diff helpers

- [ ] T021 [P] [US1] Create `src/daeyeon_bot/handlers/pr_review_schemas.py` with the Pydantic v2 `InlineComment` and `ReviewOutput` models from `contracts/claude-review-output.md` §1, including `model_config={"extra": "forbid"}` and the field constraints (line ≥1, body lengths, comments max 200).
- [ ] T022 [P] [US1] Create `src/daeyeon_bot/handlers/pr_review_diff.py` with two pure functions: `parse_hunk_ranges(patch: str) -> list[tuple[int, int]]` (extracts `(start_line, end_line)` for each `@@ … +A,B @@` hunk) and `is_anchor_in_hunk(line: int, start_line: int | None, hunks: list[tuple[int, int]]) -> bool`. Stdlib only.
- [ ] T023 [P] [US1] Add `tests/unit/test_pr_review_diff.py`: parser handles single hunk, multiple hunks, `+0,0` empty hunks; anchor check rejects out-of-hunk line, accepts in-hunk line, accepts multi-line range fully inside one hunk, rejects multi-line range spanning hunks.

### The handler

- [ ] T024 [US1] Create `src/daeyeon_bot/handlers/pr_review.py` with `MANIFEST = HandlerManifest(name="pr_review", idempotent=True, dedup_ttl=timedelta(days=1), side_effect_key=None, concurrency=1, accepts=("gh.review_requested", "pr.review.manual"))` and a `PrReviewHandler` class. Constructor takes `(manifest, gh: GhCli, persona_loader: PersonaLoader, claude_session_factory, audit_writer, config: PrReviewHandlerEntry, github_username: str)`. The `handle(event, ctx)` method runs the state machine from `data-model.md` §4 in this exact order: (a) load active persona → ValidationError ⇒ DeadLetter; (b) `gh.pr_get` to learn current author + reviewers + head SHA; (c) self-authored skip → Ack + audit `skipped_self_authored`; (d) withdrawn skip → Ack + audit `skipped_withdrawn`; (e) `gh.pr_files` → size-budget check; if exceeded post the templated "too large" Summary via `gh.post_review` + Ack + audit `skipped_too_large`; (f) lookup audit history for `(repo, pr, head_sha)`; if found and `event.payload.force is False` → Ack + audit `skipped_already_reviewed`; (g) otherwise call Claude with the assembled prompt, validate response with `ReviewOutput`, retry once on validate failure, second failure → DeadLetter; (h) `_filter_anchors` folds out-of-hunk inline comments into the Summary as bullets; (h.5) `_redact(summary, comments)` runs the structlog redaction regex set (`infra/logging.py:_REDACTION_PATTERNS`) over the summary and every comment body — any match raises `PermanentError("redaction would alter posted content")` → DeadLetter (FR-015 / SC-008 safety net; stricter than log-only redaction); (i) prepend supersede header if force-reviewing on top of a prior posted review; (j) `gh.post_review` with `event="COMMENT"`; (k) `record_supersede` + `insert_audit(status='posted', …)` + Ack. Translate `gh_cli` exceptions per `contracts/github-api-surface.md`.
- [X] T025 [US1] In `src/daeyeon_bot/app/registry.py`'s `instantiate_handler` add `if name == "pr_review":` branch that resolves `gh: GhCli`, `PersonaLoader`, `claude_session_factory`, audit writer, and the `PrReviewHandlerEntry` from the container, then returns a `HandlerRecord` for `PrReviewHandler`. Apply `_override_manifest` so config can override concurrency/accepts.
- [X] T026 [US1] In `src/daeyeon_bot/app/container.py` (or wherever `claude_session_factory` is wired), construct one shared `GhCli`, one `PersonaLoader`, and resolve `github.username` (use config value if set; otherwise call `GhCli.auth_user()` once at boot and cache). Wire all into the container.

### CLI entry point

- [X] T027 [US1] In `src/daeyeon_bot/cli/dev.py` add a `dev fire pr-review` Typer command: args `--pr <owner/repo#N | https://github.com/owner/repo/pull/N>`, `--force/-f`, `--dry-run`. Parse the PR ref, call `gh.pr_get` to fetch head SHA, build event with `type="pr.review.manual"`, payload `{repo, pr_number, head_sha, request_gen=f"manual_{int(time.time())}" if force else "0", force}`, `source_dedup_key=sha256("manual-pr-review|...").hexdigest()`. With `--dry-run`, skip outbox insertion and print the would-be event. Otherwise call `infra.outbox.insert_event` + `enqueue_handler` in one tx.

### Story-level tests

- [X] T028 [P] [US1] Add `tests/unit/test_pr_review_handler.py` using `FakeGh` + `FakeClaudeSession` + `FakePersonaLoader` + tmp_path SQLite. Cover, one test each: posts a review with summary+inline (happy path); skipped_self_authored; skipped_withdrawn; skipped_too_large posts the templated Summary; persona_unavailable → DeadLetter; Claude returns malformed JSON twice → DeadLetter; out-of-hunk anchor folded into Summary; force-supersede prepends header and updates audit `superseded_review_ids`; **redaction match in summary → PermanentError → DeadLetter (no `gh.post_review` call); redaction match in inline comment body → PermanentError → DeadLetter; clean content (no regex match) passes through unchanged and is posted** (covers FR-015 / SC-008).
- [X] T029 [P] [US1] Add `tests/integration/test_pr_review_e2e.py` mounting real `aiosqlite` against `tmp_path`, real `apply_pending_migrations` (including 002), real outbox + dispatcher, `FakeGh` + `FakeClaudeSession`. Drive end-to-end: `cli dev fire pr-review` → outbox row written → dispatcher claims → handler posts via `FakeGh.post_review` → audit row `status='posted'`. Mark `@pytest.mark.integration`.

**Checkpoint**: User Story 1 is independently shippable. Manual review works. Audit + supersede + size budget all enforced. Auto-trigger (Story 2) NOT yet wired.

---

## Phase 4: User Story 2 — Auto review on review-requested (Priority: P2)

**Story goal**: When a collaborator adds the operator as a requested reviewer, the polling trigger detects it within one polling cycle (default 300 s) and emits an event keyed on `(repo, pr, head_sha, request_gen)`. Re-requests at the same SHA increment `request_gen` and produce a fresh review. Withdrawals leave a state row with `in_pending_set=0` and emit nothing.

**Independent test (from spec.md)**: A collaborator (or second account) requests the operator's review on a PR. Within 10 min, a review appears. `daeyeon-bot inspect pr-review --pr ...` shows audit row + a `gh_review_requested_state` row with `in_pending_set=1`. Re-request at same SHA → `request_gen=2` row + new posted review.

### Polling trigger

- [X] T030 [US2] Create `src/daeyeon_bot/triggers/gh_review_requested.py` with `MANIFEST = TriggerManifest(name="gh_review_requested", source="gh_review_requested", retryable_at_source=False)` and a `GhReviewRequestedTrigger` class. Constructor: `(manifest, gh: GhCli, storage_factory, github_username, poll_interval_seconds, clock)`. `run(emit, ctx)` loops: sleep `poll_interval_seconds`; `now = clock.now_utc()`; in one async TX, call `gh.search_review_requested(github_username)` to get `now_set`; SELECT all `gh_review_requested_state` rows; for each PR in `now_set ∪ persisted` apply the case-table from `data-model.md` §5; for each "should emit" build an `Event` with `type="gh.review_requested"`, `payload={repo, pr_number, head_sha, request_gen, requested_at=now}` and `source_dedup_key=sha256("gh-review-requested|{repo}#{pr}@{sha}#{gen}").hexdigest()`; call `infra.outbox.insert_event` + `enqueue_handler("pr_review")` in the same TX as the state UPSERT (no read-modify-write across TXs). On `AuthError` from `gh`: re-raise to halt the daemon. On `RateLimitError`: skip this cycle, wait one extra `poll_interval_seconds`. On other transient: log + continue.
- [X] T031 [US2] In `src/daeyeon_bot/app/registry.py` extend `instantiate_trigger` (or its equivalent) with `if name == "gh_review_requested":` branch that resolves the dependencies above and returns the long-running task wrapper (matching existing trigger registration shape — see how `manual.py` is wired but as a real `run()` loop).
- [X] T032 [US2] In `src/daeyeon_bot/app/supervisor.py` (the existing supervisor for long-running pollers), register the trigger so its task is started by `triggers.start_all()` and parked on quarantine after 5 fails / 10 min — same contract as any other trigger.

### Story-level tests

- [X] T033 [P] [US2] Add `tests/unit/test_gh_review_requested_trigger.py` using `FakeGh` + `FakeClock` + tmp_path SQLite. Cover, one test each:
  - first observation of new PR → `state.request_gen=1`, event emitted with `gen=1`
  - same PR observed twice in a row at same SHA → second observation NO emit
  - new push (head SHA changed) → `state.request_gen=2`, event with `gen=2`
  - PR leaves search set then re-enters → `gen` increments, event with new `gen`
  - PR leaves search set permanently → state row retained with `in_pending_set=0`, no emit
  - same `(head_sha, gen)` polled twice → events UNIQUE makes the second insert a no-op (verify only one row in `events`)
  - `AuthError` from `gh.search_review_requested` propagates and halts the loop
- [X] T034 [P] [US2] Extend `tests/integration/test_pr_review_e2e.py` (or add `tests/integration/test_gh_review_requested_e2e.py`) with a flow: trigger → `gh.search_review_requested` returns one PR → state row UPSERT + event INSERT in one TX → dispatcher claims → handler posts. Then simulate re-request: `FakeGh` flips the PR out of and back into the search set → second event with `gen=2` → second posted review with supersede header. Mark `@pytest.mark.integration`.

**Checkpoint**: User Stories 1 and 2 independently shippable. Auto-trigger and manual trigger share the handler; size budget, persona, and supersede semantics all flow through the same path.

---

## Phase 5: User Story 3 — Persona governs review style and is hot-editable (Priority: P2)

**Story goal**: Operator edits `~/.claude/skills/<active>/SKILL.md` and the next review reflects the change without daemon restart. Switching the active variant is a `[handlers.pr_review].persona_skill = "<other>"` config edit + `lifecycle reload-config`.

**Why this is mostly tests**: The hot-reload primitive landed in T014 (`PersonaLoader`'s mtime-stat). The handler already calls it on every event (T024 step (a)). What's left for this story is:
1. Verifying hot-reload actually flips behavior end-to-end.
2. Surfacing the `persona_skill` swap via the existing `lifecycle reload-config` (or adding it if missing).
3. The audit row records `persona_skill` + `persona_mtime_ns` so the operator can prove which persona produced which review.

**Independent test (from spec.md)**: Run a manual review with persona body containing only "always praise the code". Run another with persona body changed to "always flag missing tests". Verify the two reviews differ in tone, and `daeyeon-bot inspect pr-review --pr ...` shows distinct `persona_mtime_ns` values for the two audit rows.

### Implementation

- [X] T035 [US3] In `src/daeyeon_bot/handlers/pr_review.py` (modifying T024) ensure every audit-write code path passes `persona_skill=loaded.name` and `persona_mtime_ns=loaded.mtime_ns` (where `loaded` is the Persona from the loader). The audit insert already has these columns from T017.
- [X] T036 [US3] In `src/daeyeon_bot/cli/lifecycle.py` (or wherever `lifecycle reload-config` lives), confirm reloading config recomposes the container so a changed `[handlers.pr_review].persona_skill` is picked up on the next event. If `reload-config` does not exist as a CLI command yet, add a thin Typer command that re-reads `config.toml` and rebuilds the registry; the daemon's existing supervisor restart is acceptable as a fallback.

### Story-level tests

- [X] T037 [P] [US3] Add `tests/unit/test_pr_review_persona_hot_reload.py`: load Persona A (mtime_ns=N1), edit on disk so mtime_ns=N2, second `load()` returns body B with mtime_ns=N2 (no daemon restart simulated; just two consecutive calls).
- [X] T038 [P] [US3] Extend `tests/integration/test_pr_review_e2e.py` with a "persona flip" scenario: handler posts review #1 with persona body "say great"; test code rewrites the SKILL.md mid-test to "say bad"; manually fire a second review event; verify Claude was called with the new body (FakeClaudeSession records the system-prompt argument; assert it changed) and audit row #2 has different `persona_mtime_ns` than row #1.

**Checkpoint**: Persona hot-edit verified end-to-end. Audit history proves which persona produced which review.

---

## Phase 6: User Story 4 — Operator pause kill-switch (Priority: P3)

**Story goal**: `daeyeon-bot lifecycle pause` blocks all review posting; queued events stay queued; `lifecycle resume` drains them with first review posting within 5 min.

**Why minimal new code**: The PAUSE flag is already implemented (Phase 3 of the daemon). It blocks Claude calls before rate-limit check (`CONTRACTS.md` §5). Since the handler reaches `claude_session_factory()` BEFORE `gh.post_review`, the existing kill-switch already short-circuits review posting. This phase is verification + the audit-status story for held requests.

**Independent test (from spec.md)**: With a queued review-requested event, run `lifecycle pause`. Trigger fires; outbox row goes to `running` then handler hits PAUSE check (`QuotaError` from `claude_session_factory`); dispatcher schedules `Retry`. Resume → next dispatcher cycle → review posts.

### Implementation

- [X] T039 [US4] In `src/daeyeon_bot/handlers/pr_review.py` (modifying T024) confirm that `QuotaError` raised by `claude_session_factory()` (the existing PAUSE-check path) propagates as a `Retry` via the dispatcher's exception mapping. If the size-budget "too large" path or any other early-Ack path bypasses the PAUSE check, restructure so PAUSE is honored BEFORE the bot calls `gh.post_review` for the templated Summary too. Add a single guard `if pause.is_active(): raise QuotaError("paused")` near the top of `handle()` so even the size-budget path waits for resume.

### Story-level tests

- [X] T040 [P] [US4] Add `tests/integration/test_pr_review_pause.py`: with PAUSE active, fire a `pr.review.manual` event; verify outbox row sits in `retry` (no `gh.post_review` call recorded by `FakeGh`); clear PAUSE; next dispatcher cycle posts the review and audit row appears with `status='posted'`. Mark `@pytest.mark.integration`.

**Checkpoint**: All four user stories pass independent tests. The bot ships.

---

## Phase 7: Polish & cross-cutting concerns

**Purpose**: Inspector CLI, config-example completeness, prune wiring, runbook entry, coverage validation.

- [X] T041 [P] In `src/daeyeon_bot/cli/inspect.py` add a `inspect pr-review` subcommand: `--pr owner/repo#N` shows audit-row history (newest first) with status, review_id, submitted_at, persona, supersede chain. No flags shows the most recent 20 audit rows across all PRs.
- [X] T042 [P] In `src/daeyeon_bot/app/prune.py` (or its retention runner) call `pr_review_state.prune_dormant(conn, older_than_iso=now - retention.gh_state_dormant_days days)` as part of the existing prune pass. Add a single test in `tests/unit/test_prune.py` (or alongside existing prune tests) that a dormant row past the threshold gets deleted while a recent dormant row stays.
- [X] T043 [P] Add a "PR review (feature 001)" section to `docs/RUNBOOK.md` covering: how to inspect audit history (`daeyeon-bot inspect pr-review`), how to fix `persona unavailable` DLQ entries, how to raise the size budget, and what to do if `gh auth status` breaks (operator runs `gh auth refresh`, daemon resumes on next boot).
- [X] T044 Update `docs/PLAN.md` §4.1 schema dump to include the two new tables from migration 002, and update CLAUDE.md "Current state — what's actually built" with a Phase 7 row mentioning the PR-review feature lands behind feature flag `[handlers.pr_review].enabled`.
- [X] T045 Run `just check` (lint + typecheck + test) and confirm coverage targets are met: `core/pr_review/**` ≥ 90%, `infra/pr_review_*.py` + `infra/gh_cli.py` ≥ 80%, new `cli/dev.py` additions ≥ 60%, new `triggers/gh_review_requested.py` + `handlers/pr_review.py` ≥ 85%. Fix shortfalls before declaring done.
- [ ] T046 Run the `quickstart.md` flow against a real test PR (manual smoke). Verify Summary first line names the head SHA, ≥1 inline comment lands on the right line, audit row appears, `--force` produces the supersede header. Capture the GitHub permalinks in the PR description for the merge. **(Deferred: requires real GitHub PR + operator action; tracked separately from auto-mode implementation)**

---

## Dependencies

```
Phase 1 (Setup) ─── Phase 2 (Foundational) ─┬─► Phase 3 (US1, P1) ─┐
                                            │                     │
                                            ├─► Phase 4 (US2, P2) ┤
                                            │                     ├─► Phase 7 (Polish)
                                            ├─► Phase 5 (US3, P2) ┤
                                            │                     │
                                            └─► Phase 6 (US4, P3) ┘
```

- **Phase 1 → Phase 2**: T003 (migration) must land before T017–T020 (audit/state CRUD have FK to `events.id` already in 001 + tables added in 002).
- **Phase 2 blocks Phases 3–6**: every user story imports from `core/pr_review/`, `infra/gh_cli.py`, `infra/pr_review_persona.py`, `infra/pr_review_audit.py`, `infra/pr_review_state.py`, and the config models in `app/config.py`.
- **Phases 3, 4, 5, 6 are independent** (within Phase 2's prerequisites). After Phase 2 lands, US1, US2, US3, US4 can proceed in parallel by different contributors.
  - US3 reuses T014 (PersonaLoader) and T024 (handler), so it gates on US1 (handler exists) but its TESTS are independent.
  - US4 reuses the existing PAUSE primitive + T024; small tweak in T039.
- **Phase 7 polish runs last** — depends on all stories being feature-complete.

---

## Parallel execution opportunities

### Within Phase 2 (foundational)

After T003 + T009 land, these are independent:

```
[P] T005  core/pr_review/types.py
[P] T006  core/pr_review/persona.py
[P] T007  core/pr_review/audit.py
[P] T010  test_config.py extensions
[P] T012  tests/fakes/gh_cli.py
[P] T013  tests/unit/test_gh_cli.py
[P] T015  tests/fakes/pr_persona.py
[P] T016  tests/unit/test_pr_review_persona.py
[P] T018  tests/unit/test_pr_review_audit.py
[P] T020  tests/unit/test_pr_review_state.py
```

T011/T014/T017/T019 (the production adapters) are sequential within their own files but do not block each other.

### Within Phase 3 (US1)

```
[P] T021  pr_review_schemas.py
[P] T022  pr_review_diff.py
[P] T023  test_pr_review_diff.py
[P] T028  test_pr_review_handler.py    # after T024 is testable
[P] T029  test_pr_review_e2e.py
```

T024 (handler) and T025/T026/T027 (registry/container/CLI wiring) are the critical path.

### Within Phase 4 (US2)

T030 → T031 → T032 are sequential (each depends on the previous file's exports). Tests T033, T034 run in parallel after T030 lands.

### Across phases (different developers)

After Phase 2 closes, three contributors can work in parallel: one on US1 (Phase 3), one on US2 (Phase 4), one on US3+US4 polish (Phases 5–6). Phase 7 then sweeps everything.

---

## Implementation strategy

**MVP scope = Phase 1 + Phase 2 + Phase 3 (US1).** That alone delivers the spec's P1 user story:
- Operator manually triggers a review on any PR.
- Persona-driven Summary + inline comments posted via single review object.
- Self-authored / withdrawn / too-large short-circuits work.
- Force re-review with chronological supersede works.
- Audit history queryable.

Ship Phase 3, then add Phase 4 (auto-trigger) as the headline second increment. Phase 5 (persona hot-edit verification) and Phase 6 (pause integration) are confidence builders — they ship close to Phase 4 with minimal new code.

**Anti-goals during execution**:
- Do NOT introduce new dependencies (`httpx`, `PyGithub`, etc.). Stay on `gh` subprocess + existing deps.
- Do NOT edit `001_init.sql`. Migration 002 is purely additive.
- Do NOT bypass `infra/outbox.py:insert_event` + `enqueue_handler`. The polling trigger MUST go through outbox so the at-least-once + recovery contract holds.
- Do NOT register a new Keychain entry for GitHub. `gh auth token` is the single source of truth.
- Do NOT post any GitHub endpoint not listed in `contracts/github-api-surface.md` §"Endpoints used".

---

## Format validation

Every task above follows: `- [ ] T### [P?] [USx?] Description with file path`.

- Setup phase (T001–T004): no story label. ✓
- Foundational phase (T005–T020): no story label. ✓
- US1 phase (T021–T029): all carry `[US1]`. ✓
- US2 phase (T030–T034): all carry `[US2]`. ✓
- US3 phase (T035–T038): all carry `[US3]`. ✓
- US4 phase (T039–T040): all carry `[US4]`. ✓
- Polish phase (T041–T046): no story label. ✓
- Every task names a concrete file path or modification target. ✓
