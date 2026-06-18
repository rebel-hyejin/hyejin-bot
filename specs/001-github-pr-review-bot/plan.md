# Implementation Plan: GitHub PR Review Automation Bot

**Branch**: `001-github-pr-review-bot` | **Date**: 2026-05-04 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-github-pr-review-bot/spec.md`

## Summary

When the operator is requested as a reviewer on a GitHub pull request, the bot
auto-generates a persona-driven review and posts it via the GitHub Pull Request
Review API as a single review object — Summary in the review body plus inline
comments anchored to file/line. Manual triggering of any PR by URL is also
supported. Authentication delegates to the operator's local `gh` CLI; no new
secret enters the daemon's Keychain/0600 stack.

The implementation extends the existing daemon by adding **two triggers** (a 5-min
polling trigger for `review-requested:@me` and the existing manual one wired to a
new event type) and **one handler** (`pr_review`) that loads the active persona
SKILL.md, fetches PR data via `gh api`, calls Claude for structured review
output, validates it, and posts the review as one atomic API call. A new SQLite
table `gh_review_requested_state` lets the polling trigger detect re-requests at
the same head SHA (request_gen state machine), and the existing
`(source, source_dedup_key)` UNIQUE on `events` carries the deterministic dedup
token `sha256("gh-review-requested|{repo}#{pr}@{sha}#{gen}")`.

## Technical Context

**Language/Version**: Python 3.12 (`requires-python = ">=3.12,<3.13"` in `pyproject.toml`).
**Primary Dependencies**: existing — `claude-agent-sdk`, `pydantic` (v2), `pydantic-settings`, `structlog`, `aiosqlite`, `typer`, `keyring`, `uuid-utils`. No new runtime deps; GitHub access goes through the operator's local `gh` CLI via subprocess.
**Storage**: SQLite WAL (existing `state.db`). One additive migration: `002_gh_review_requested_state.sql`.
**Testing**: pytest (`pytest-asyncio` mode=auto), pytest-cov; integration tests use real `aiosqlite` against `tmp_path` DBs and fakes for `gh`/Claude.
**Target Platform**: macOS (launchd) + Linux (systemd) — same artifact, same code paths.
**Project Type**: single-process daemon (existing `src/hyejin_bot/`), not split.
**Performance Goals**: SC-001 (manual: 95% under 5 min) + SC-002 (auto: 95% under 10 min). Polling cadence 5 min ⇒ p50 detection ~2.5 min, p95 ~5 min. Dispatcher already polls outbox every ~200 ms.
**Constraints**: Size budget = 1000 changed lines OR 50 changed files (config-overridable). `gh` REST budget ≈ 5000 req/hr per user, well above per-poll cost (1 search + 0..N PRs × ~3 endpoints). Boot adds ≤200 ms (one `gh auth status` probe). No new persistent secret.
**Scale/Scope**: One operator. Up to ~20 simultaneous review-requested PRs in steady state; long-tail only when on-call rotation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The repo has no `.specify/memory/constitution.md`. The de-facto constitution
is the set of stable contracts in `CLAUDE.md`, `CONTRACTS.md`, and `docs/PLAN.md`.
This plan is gated against those:

| Gate | Source | Status | Notes |
|---|---|---|---|
| Single-tenant, single-process | CLAUDE.md "What this is" | PASS | No multi-tenancy, no broker, no API-key auth introduced. |
| Module layering one-way (`core ← infra ← triggers/handlers ← app ← cli`) | CLAUDE.md §Module layering | PASS | New trigger / handler stay in their layers; SQLite ops via `infra/`. |
| One event = one transaction (no read-modify-write) | CLAUDE.md §One event, one cycle | PASS | Trigger updates `gh_review_requested_state` + writes `events`+`outbox` in one tx. |
| Boot order fixed | CLAUDE.md §Boot order | PASS | Polling trigger registers via existing `app/registry.py:_instantiate_trigger`; no boot-step reorder. |
| At-least-once + idempotent handler | CONTRACTS.md §1 | PASS | `pr_review` is `idempotent=True`; `side_effect_key=None` (GitHub review API has no idempotency key, but per-(repo,pr,sha,gen) dedup happens BEFORE posting via `event_review_audit` lookup). |
| Outbox claim-row pattern | CONTRACTS.md §1 | PASS | New trigger uses `infra/outbox.py:insert_event` + `enqueue_handler`; new handler returns `Ack`/`Retry`/`DeadLetter` only. |
| HandlerResult is the only exit | CONTRACTS.md §2 | PASS | `gh` CLI 401/403 → `AuthError` → daemon halt (exit 78); 5xx/timeout → `TransientError`/`Retry`; size-budget overflow → `Ack` with "too large" Summary; missing persona → `DeadLetter`. |
| Migration linear/additive | CLAUDE.md §Add a SQL column | PASS | One new file `002_gh_review_requested_state.sql` + adds tracking + audit tables. No edits to `001_init.sql`. |
| Secrets discipline | CLAUDE.md §Secrets discipline | PASS | No new Keychain entry. `gh` CLI is queried at boot and on cache-miss; token never lands in `os.environ` or logs. Existing structlog redaction processor catches accidents. |
| Registry: explicit `if name == ...` | CLAUDE.md §Add new handler/trigger | PASS | `pr_review` and `gh_review_requested` get explicit branches in `app/registry.py`. |

**Violations**: none. **Complexity Tracking** section below is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-github-pr-review-bot/
├── plan.md                        # this file
├── research.md                    # Phase 0
├── data-model.md                  # Phase 1
├── quickstart.md                  # Phase 1
├── contracts/                     # Phase 1
│   ├── github-api-surface.md      # exact gh endpoints + JSON shapes used
│   ├── claude-review-output.md    # JSON schema Claude must emit + validator
│   └── persona-skill-format.md    # SKILL.md frontmatter+body contract
├── checklists/
│   └── requirements.md            # already created by /speckit.specify
└── tasks.md                       # /speckit.tasks output (NOT this command)
```

### Source Code (repository root)

Existing layout (unchanged dirs are abbreviated). New files marked **NEW**.

```text
src/hyejin_bot/
├── core/                                    # pure domain — stdlib only
│   ├── events.py                            # (existing) Event dataclass
│   ├── manifest.py                          # (existing)
│   ├── protocols.py                         # (existing)
│   ├── results.py                           # (existing)
│   └── pr_review/                           # NEW: domain types for this feature
│       ├── __init__.py                      # NEW
│       ├── types.py                         # NEW: PullRequest, ChangedFile, ReviewOutput, InlineComment dataclasses
│       └── persona.py                       # NEW: Persona dataclass (skill_dir, body, mtime_ns)
├── infra/                                   # adapters — depend on core
│   ├── outbox.py                            # (existing) — REUSED as-is
│   ├── storage.py                           # (existing)
│   ├── claude.py                            # (existing)
│   ├── gh_cli.py                            # NEW: thin async wrapper around `gh api`/`gh auth token`
│   ├── pr_review_persona.py                 # NEW: SKILL.md loader with mtime cache + frontmatter strip
│   ├── pr_review_state.py                   # NEW: gh_review_requested_state CRUD (atomic UPSERT)
│   ├── pr_review_audit.py                   # NEW: pr_review_audit CRUD (force-supersede history)
│   └── db/migrations/
│       ├── 001_init.sql                     # (existing) — DO NOT EDIT
│       └── 002_gh_review_requested_state.sql  # NEW
├── triggers/
│   ├── manual.py                            # (existing)
│   └── gh_review_requested.py               # NEW: 5-min polling loop, request_gen state machine
├── handlers/
│   ├── echo.py                              # (existing)
│   └── pr_review.py                         # NEW: load persona → fetch PR → call Claude → post review
├── app/
│   ├── registry.py                          # MODIFIED: add `pr_review` and `gh_review_requested` branches
│   ├── config.py                            # MODIFIED: add `GitHubConfig`, `PrReviewHandlerEntry`, `GhReviewRequestedTriggerEntry`
│   └── ...                                  # (other existing files: untouched)
└── cli/
    ├── dev.py                               # MODIFIED: add `dev fire pr-review --pr <url> [--force]`
    └── ...                                  # (other existing files: untouched)

config.example.toml                          # MODIFIED: add [triggers.gh_review_requested], [handlers.pr_review], routing entry, [github]

tests/
├── unit/
│   ├── test_pr_review_persona.py            # NEW
│   ├── test_pr_review_state.py              # NEW
│   ├── test_pr_review_handler.py            # NEW (with FakeGh + FakeClaudeSession)
│   └── test_gh_review_requested_trigger.py  # NEW (with FakeClock + FakeGh)
├── integration/
│   └── test_pr_review_e2e.py                # NEW (real aiosqlite + fakes for gh/claude, exercises outbox+settle)
└── fakes/
    ├── gh_cli.py                            # NEW: in-memory `gh` substitute returning canned PR/diff JSON
    └── pr_persona.py                        # NEW: helper to materialize SKILL.md fixtures
```

**Structure Decision**: extend the existing single-project layout. The new
feature lands as **two new triggers + one new handler + one additive migration
+ one config section**, plus a small set of `infra/pr_review_*.py` adapters.
This matches `CLAUDE.md` "Add a new handler / Add a new trigger / Add a SQL
column" recipes exactly. No new top-level package; no split into web app /
mobile / etc.

**Implementation scope estimate**: spec FR-013's 1000-lines / 50-files
threshold is the runtime PR-diff budget (what the bot will refuse to review),
NOT a cap on this feature's source code. As a self-imposed scope anchor for
this feature's implementation, we target ~12 source files
(`triggers/gh_review_requested.py`, `handlers/pr_review.py`, 4×
`infra/pr_review_*` / `infra/gh_cli.py`, 2× `core/pr_review/`, 1 SQL
migration, 1 config edit, 1 CLI edit, 1 registry edit) + ~5 test files + 2
fakes ≈ **20 files / ~900 lines**. Anything materially larger should
prompt a re-scope discussion.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

(none)

## Post-design Constitution re-check

After Phase 1 (data-model + contracts), gates re-evaluated. No change from
the pre-Phase-0 verdict:

- New tables `gh_review_requested_state`, `pr_review_audit` are additive in a
  single new migration `002_*.sql`. No edit to `001_init.sql`.
- All trigger writes go through `infra/outbox.py:insert_event` +
  `enqueue_handler` plus a single SQLite tx that also UPSERTs the state row
  — no read-modify-write in app code.
- Handler returns only `Ack`/`Retry`/`DeadLetter`; `gh` 401 ⇒ `AuthError` ⇒
  daemon halt (exit 78).
- No new persistent secret. `gh auth token` is fetched on-demand and never
  written anywhere.
- The 5 endpoints in `contracts/github-api-surface.md` are the entire
  GitHub surface; FR-010b ("never modify the PR") is enforced by code-review
  against that file.
- Banned-imports stays clean: `core/pr_review/` is stdlib-only;
  `infra/pr_review_*.py` only imports from `core/`.

**Verdict**: PASS. Phase 2 (`/speckit.tasks`) can proceed.
