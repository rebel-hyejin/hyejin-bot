# daeyeon-bot

> Personal Claude bot daemon for daeyeon.lee@rebellions.ai. Runs 24/7 on
> macOS (launchd) or a Linux server (systemd --user), wakes up on triggers
> (manual / cron / webhook / file watch / Slack), and dispatches each event
> to a handler that calls Claude on the operator's Pro/Max OAuth subscription.
> **Not a SaaS, not multi-tenant, not for anyone else.**

## Status

Phases 0–6 implemented (vertical slice → reliability → operability → real
SDK + secrets → deployment → hardening). See `docs/PLAN.md` for the design
and `docs/RUNBOOK.md` for operations.

## Quick start (dev)

```bash
just sync                     # uv sync (creates .venv, installs deps)
cp config.example.toml config.toml
cp .env.example .env
just check                    # lint + typecheck + test
just run                      # daeyeon-bot run (foreground)
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
- **2-phase shutdown** (180 s budget): drain in-flight, mark interrupted, exit.
- **Secrets** via macOS Keychain or 0600 file. Never committed, never in env after startup.
- **Self-alerting heartbeat**: tick lag > 3× threshold → ERROR log to journald / launchd-stderr.

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

## Operations

Routine ops, Mac/Linux parity table, and incident playbooks (corrupt
SQLite, token revocation, hung daemon, pidfile lock conflict, disk full)
live in **[`docs/RUNBOOK.md`](docs/RUNBOOK.md)**.

Most-used commands:

```bash
just doctor                  # pre-flight checks
just status                  # heartbeat / outbox / PAUSE / pidfile
just backup                  # hot SQLite snapshot
just prune                   # apply retention
just install-mac             # register launchd agent (macOS)
just install-linux <token>   # register systemd --user unit (Linux)
```

Exit codes that wrappers care about:

| Code | Name           | Meaning                                       | Auto-restart? |
|------|----------------|-----------------------------------------------|---------------|
| 0    |                | clean shutdown                                | yes (KeepAlive) |
| 75   | EX_TEMPFAIL    | another instance holds the pidfile lock       | yes           |
| 78   | EX_CONFIG      | `AuthError` / `ConfigError` — operator action | **no**        |

## License

Proprietary, internal-use-only. Not for redistribution.
