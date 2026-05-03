# daeyeon-bot

> Personal Claude bot daemon for daeyeon.lee@rebellions.ai. Runs 24/7 on macOS (launchd) or sane Linux server (systemd), wakes up on triggers (manual / cron / webhook / file watch / Slack), and dispatches each event to a handler that calls Claude on the operator's Pro/Max OAuth subscription. **Not a SaaS, not multi-tenant, not for anyone else.**

## Status

Phase 0 — scaffolding only. Boot-up, persistence, and real handlers come in subsequent phases. See `docs/PLAN.md`.

## Quick start (dev)

```bash
just sync                     # uv sync (creates .venv, installs deps)
cp config.example.toml config.toml
cp .env.example .env
just lint                     # ruff check + format
just test                     # pytest
daeyeon-bot --help
```

## Architecture

See `docs/PLAN.md` for the full design. One-paragraph summary:

```
trigger → outbox (SQLite WAL) → dispatcher → handler → ClaudeSession
                                                           ↓
                                                  outbox.settle()
```

- **At-least-once** delivery. Handlers must be idempotent or accept dedup_keys.
- **Single instance** enforced via pidfile + flock.
- **2-phase shutdown** (180s budget): drain in-flight, mark interrupted, exit.
- **Secrets** via macOS Keychain or 0600 file. Never committed, never in env after startup.

## Layout

```
src/daeyeon_bot/
├── core/        # domain types (events, results, manifest, protocols)
├── infra/       # SDK, SQLite, secrets, structlog, migrations
├── triggers/    # how events come in (manual, cron, webhook, …)
├── handlers/    # what we do with them (echo, pr-self-review, …)
├── app/         # composition: container, dispatcher, lifecycle, supervisor
└── cli/         # Typer entry points (run, inspect, ops, dev, lifecycle)
```

## Running as a daemon

Phase 5 will add `just install-mac` and `just install-linux`. For now, `daeyeon-bot run` foreground only.

## License

Proprietary, internal-use-only. Not for redistribution.
