# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal Claude bot **daemon** for one operator (daeyeon.lee@rebellions.ai). Runs 24/7, wakes on triggers (manual / cron / webhook / file watch / Slack), dispatches each event to a handler that calls Claude on the operator's Pro/Max OAuth subscription. **Not SaaS, not multi-tenant, not multi-process.** Treat any change that introduces multi-tenancy, API-key auth, message brokers, or container orchestration as out of scope unless `docs/PLAN.md` is updated first.

Currently in **Phase 1** (vertical slice: `manual → outbox → echo`). See `docs/PLAN.md` §5 for phase boundaries — Phases 2–6 are unimplemented; many CLI subcommands intentionally raise `NotImplementedError`.

## Common commands

The single source of truth for tasks is `justfile`:

```bash
just sync              # uv sync --all-extras --dev
just lint              # ruff check + ruff format --check (src + tests)
just format            # ruff format + ruff check --fix
just typecheck         # pyright (strict mode)
just test              # pytest with coverage; passes args through
just test-unit         # only tests/unit
just test-integration  # only tests marked `integration`
just check             # lint + typecheck + test (pre-commit aggregate)
just run               # daeyeon-bot run (foreground daemon)
just doctor            # daeyeon-bot ops doctor
just migrate           # apply DDL migrations
```

Single test: `uv run pytest tests/unit/test_outbox.py::test_claim_one_atomic -x`. Integration tests need `-m integration` and currently use real `aiosqlite` against tmp paths — they don't hit the network.

`uv` is the package manager (not pip/poetry). `pyright` runs in **strict** mode (`pyproject.toml:91`) — type errors block merge.

## Architecture invariants

Read `docs/PLAN.md` and `CONTRACTS.md` before changing any of the following. They are stable interfaces — changes require an explicit migration plan.

### Module layering (one-way deps)

```
core/      pure domain (dataclasses, protocols). NO outside deps except stdlib.
infra/     storage / SDK / logging / secrets. Depends on core only.
triggers/  emit Event via core.protocols.Trigger.
handlers/  consume Event via core.protocols.Handler.
app/       composition root: container, registry, dispatcher, lifecycle, lock, supervisor.
cli/       Typer entry points. Five files; no business logic.
```

Banned-imports (`tool.ruff.lint TID`) is on — adding a cross-layer import that violates this layering will fail lint.

### Boot order is fixed

`app/lifecycle.py:boot()` follows the order documented in `PLAN.md` §2.3 and that file's docstring. **Do not reorder steps.** In particular, `outbox.recover_interrupted_rows()` MUST run after migrations and before the dispatcher's poll loop — otherwise crashed-mid-flight rows can be re-claimed before recovery decides retry-vs-DLQ.

### 2-phase shutdown (180s budget)

`app/lifecycle.py` orchestrates Phase A (stop claiming) → Phase B (drain in-flight, `PHASE_B_BUDGET_S = 120s`) → Phase C (WAL checkpoint, release pidfile lock). The dispatcher exposes `request_stop_claiming()` / `drain(timeout)` / `stop()` — that triplet is the contract. Tests live in `tests/integration/test_two_phase_shutdown.py`.

### At-least-once delivery + idempotency

Every handler MUST tolerate being invoked more than once for the same `Event`. The dispatcher writes a `dedup_keys` row keyed on `(event_id, handler, attempt_epoch)` only after `Ack` from an `idempotent=True` handler. A non-idempotent handler that ends `interrupted` goes to `dead_letter` — operator must `ops replay --confirm`. See `CONTRACTS.md` §1–2.

### Outbox claim-row pattern

`infra/outbox.py:claim_one()` is the only sanctioned way to mark a row `running`. It uses `UPDATE … WHERE claimed_by IS NULL` for race safety. Do not bypass this — even in tests, prefer `claim_one` so race conditions are exercised.

### HandlerResult is a sum type

```
HandlerResult = Ack | Retry(after_s) | DeadLetter(reason)
```

Defined in `core/results.py`. Exception → result mapping is centralized in `app/dispatcher.py:_run_one`:

| Raised | Becomes |
|---|---|
| `RateLimitError` | `Retry(RATE_LIMIT_BACKOFF_S)` |
| `TransientError` | `Retry(DEFAULT_BACKOFF_S)` |
| `AuthError` | dispatcher halts (`stop()`); row stays `running` until next boot recovers |
| `PermanentError` / unclassified | `DeadLetter(repr(exc))` |

Do not catch and translate exceptions inside handlers — return `Retry`/`DeadLetter` directly or raise the typed error from `core.errors`.

### SQLite contract

- All connections must apply `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000; foreign_keys=ON`. `infra/storage.py:open_db` enforces this; never construct an `aiosqlite.connect()` directly.
- DDL lives in `src/daeyeon_bot/infra/db/migrations/NNN_*.sql`. **Linear, additive, never rewritten in place.** `meta.schema_version` is the only source of truth.
- Rate-limit token decrement must be a single atomic `UPDATE` (`CONTRACTS.md` §5). Read-modify-write in app code is wrong.

### Secrets discipline (Phase 4 — partially implemented)

- OAuth token never goes through `os.environ` after startup.
- Provider order: Keychain (macOS) → 0600 file (Linux) → env (only with `--insecure-env`).
- Logging redaction (`infra/logging.py`) is required before any handler logs Claude payloads in Phase 4. Until then, do not log payloads beyond the existing previews.

## Configuration model

- `config.toml` is **not committed** (see `.gitignore`). `config.example.toml` is the reference; copy it.
- pydantic-settings env override prefix: `DAEYEON_BOT__`, nested delimiter `__` (e.g. `DAEYEON_BOT__LOGGING__LEVEL=DEBUG`).
- `Config.routing[event_type] = [handler_name, ...]` is the only routing table — no decorator-based discovery.
- New handlers register through the explicit `if name == ...` block in `app/registry.py:_instantiate_handler`. This is intentional; do not introduce entry-points or import-time side effects.

## Testing patterns

- `tests/fakes/` is a real package (FakeClock, FakeClaudeSession, etc. — being grown phase by phase). Reach for fakes before mocks.
- Integration tests rely on the real `aiosqlite` + migrations stack with tmp_path DBs; they're fast enough not to need mocking.
- `pytest-asyncio` mode is `auto` — async tests don't need `@pytest.mark.asyncio`.
- Coverage targets (per PLAN §6.3): core/app ≥90%, infra ≥80%, cli ≥60%.
- New handler test recipe: unit test with `FakeClaudeSession` + `FakeClock`, then (if the handler talks to outbox/dispatcher) one integration test under `tests/integration/`.

## Runtime layout

- State directory: `~/.daeyeon-bot/` (config knob `runtime.state_dir`).
  - `state.db` — SQLite WAL.
  - `daeyeon-bot.pid` — pidfile + flock for single-instance enforcement (`app/lock.py`).
  - `PAUSE` — operator kill-switch (Phase 3); presence blocks Claude calls before rate-limit check.
- Exit codes the CLI returns: `75` (EX_TEMPFAIL) when another instance holds the lock; `78` (EX_CONFIG) on `AuthError` (Phase 4 wires this).

## When making changes

- New trigger or handler? Update `app/registry.py`, `config.example.toml` `[handlers.X]` + `[routing]`, and add the `MANIFEST` constant in the module — those three together are the contract.
- Touching `infra/outbox.py` or `app/dispatcher.py`? Re-read `CONTRACTS.md` first; these files implement the at-least-once + recovery semantics.
- Adding a SQL column? New migration file `infra/db/migrations/NNN_*.sql`. Never edit existing migrations.
- A change that affects boot order or shutdown phases must update both `lifecycle.py`'s docstring and `docs/PLAN.md` §2.3/§2.4 in the same commit.
