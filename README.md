# hyejin-bot

> Personal Claude bot **daemon** for one operator (hyejin.han@rebellions.ai).
> Runs 24/7 on macOS (launchd) or a Linux server (systemd --user), wakes up
> on triggers, dispatches each event to a handler, and calls Claude on the
> operator's Pro/Max OAuth subscription. **Single-tenant, single-process,
> single-host.** Not a SaaS, not for anyone else.

> Fork of [`rebel-daeyeonlee/daeyeon-bot`](https://github.com/rebel-daeyeonlee/daeyeon-bot)
> — attribution preserved. Upstream code-review persona kept under
> `.claude/skills/upstream-code-review-reference/` as reference;
> hyejin's persona at `.claude/skills/hyejin-bot-code-review/`.

## Status

Phases 0–7 implemented (vertical slice → reliability → operability → real
SDK + secrets → deployment → hardening → GitHub PR-review bot → Jira
regression-failure triage bot). Built-in triggers: `manual`,
`gh_review_requested`, `jira_assigned`. Built-in handlers: `echo`,
`pr_review`, `jira_triage`. Other workloads are added one trigger/handler
at a time.

| Feature | Spec | Quickstart |
|---|---|---|
| GitHub PR review bot | [`specs/001-github-pr-review-bot/`](specs/001-github-pr-review-bot/) | [`quickstart.md`](specs/001-github-pr-review-bot/quickstart.md) |
| Jira regression triage bot | [`specs/002-jira-triage-bot/`](specs/002-jira-triage-bot/) | [`quickstart.md`](specs/002-jira-triage-bot/quickstart.md) |

| Doc | Purpose |
|---|---|
| `docs/PLAN.md`    | Full design — architecture, phased plan, schemas. |
| `CONTRACTS.md`    | Stable interfaces (delivery semantics, HandlerResult, manifests). |
| `CLAUDE.md`       | Code-level guardrails for editors (humans + AI). |
| `docs/DEPLOY.md`  | Fresh-machine operator guide — token, install, smoke. |
| `docs/RUNBOOK.md` | Routine ops, Mac/Linux parity, incident playbooks. |

---

## Mental model — one event, end to end

```
 ┌──────────┐   emit_one()    ┌────────────┐  claim_one()   ┌────────────┐
 │ trigger  │ ──────────────▶ │   outbox   │ ─────────────▶ │ dispatcher │
 │  manual  │   Event row →   │  (SQLite)  │  ← row marked  │  poll loop │
 │  cron …  │                 │  WAL mode  │     running    │            │
 └──────────┘                 └────────────┘                └─────┬──────┘
                                                                  │ HandlerResult
                                                                  ▼
                                  ┌─────────────────┐      ┌──────────────┐
                                  │ outbox.settle() │ ◀──  │   handler    │
                                  │ acked / retry / │      │  (echo, …)   │
                                  │  dead_letter    │      └──────┬───────┘
                                  └─────────────────┘             │
                                                                  ▼ optional
                                                           ┌──────────────┐
                                                           │ ClaudeSession│
                                                           │ (OAuth, SDK) │
                                                           └──────────────┘
```

1. **Trigger** wakes up (cron tick, file event, CLI command, …) and writes
   one row to the `events` table plus N rows to `outbox` — one per handler
   the routing table maps the event to.
2. **Dispatcher** polls the outbox every ~200 ms. `claim_one()` is a single
   atomic `UPDATE … WHERE claimed_by IS NULL` so two pollers (or two boots)
   never claim the same row.
3. **Handler** receives the `Event`, optionally calls Claude through the
   `ClaudeSession` protocol, returns `Ack | Retry | DeadLetter`.
4. **Dispatcher settles** the outbox row (`acked` / `retry` / `dead_letter`)
   and — for idempotent handlers that returned `Ack` — writes a `dedup_keys`
   row keyed on `(event_id, handler, attempt_epoch)`.

If the daemon crashes mid-flight, on the next boot
`outbox.recover_interrupted_rows()` finds anything still marked `running`
and either re-queues it (idempotent handler) or moves it to `dead_letter`
(non-idempotent — operator must `ops replay --confirm`).

---

## Guarantees the daemon makes

- **At-least-once delivery.** A handler is invoked one or more times for
  every event. Idempotency is the handler's job; the dispatcher only
  guarantees no row is lost.
- **Single instance.** `~/.hyejin-bot/hyejin-bot.pid` + `flock(2)`. Trying
  to start a second daemon exits 75.
- **2-phase shutdown** (180 s budget). Phase A stops claiming new rows,
  Phase B drains in-flight handlers (≤120 s), Phase C checkpoints WAL and
  releases the pidfile lock.
- **Secrets isolation.** OAuth token comes from macOS Keychain (Mac) or a
  0600 file (Linux), never from `os.environ` after startup, never
  committed.
- **Self-alerting heartbeat.** Tick lag > 3× threshold → ERROR log emitted
  to journald / launchd-stderr so a hung daemon flags itself.
- **Hot SQLite snapshots.** `just backup` uses `Connection.backup()` to
  copy state.db while the daemon runs; pruned to `retention.backup_keep`.

---

## Quick start (dev)

```bash
just sync                                  # uv sync (creates .venv)
cp config.example.toml config.toml         # gitignored; edit freely
cp .env.example .env                       # dev overrides only
just check                                 # lint + typecheck + tests
just migrate                               # create state.db with current schema
just setup-token                           # paste token → Keychain (Mac)
just doctor                                # all ✓?
just run                                   # foreground daemon, Ctrl-C exits
```

In another terminal — fire one event end-to-end:

```bash
hyejin-bot dev fire manual -m 'hello'     # writes event + enqueues handlers
hyejin-bot inspect events ls              # see the row that was just written
hyejin-bot inspect status                 # outbox depths + in-flight + quarantine
```

---

## Run as a daemon

### macOS (launchd)
```bash
just install-mac          # ~/Library/LaunchAgents/ai.rebellions.hyejin-bot.plist
launchctl list | grep hyejin-bot    # PID present → alive
tail -f ~/.hyejin-bot/launchd.err.log
```
KeepAlive restarts the process if it dies. `RestartPreventExitStatus=78`
(via the wrapper) means an `AuthError` correctly halts the loop until the
operator rotates the token.

### Linux server (systemd --user)
```bash
umask 077
printf '%s' '<token>' > ~/.config/hyejin-bot/oauth_token
just install-linux ~/.config/hyejin-bot/oauth_token
journalctl --user -u hyejin-bot -f
```
Type=notify + `WatchdogSec=120` ties our heartbeat into systemd's own
watchdog.

Routine ops, the parity table, and incident playbooks (corrupt SQLite,
token revocation, hung daemon, lock conflict, disk full) live in
[`docs/RUNBOOK.md`](docs/RUNBOOK.md).

---

## Layout

```
src/hyejin_bot/
├── core/        # pure domain — events, results, manifests, protocols, errors
├── infra/       # adapters — sqlite, secrets, structlog, claude SDK, migrations
├── triggers/    # how events come in (manual today; cron / webhook / slack later)
├── handlers/    # what we do with them (echo today; pr-review / digest later)
├── app/         # composition — container, registry, dispatcher, lifecycle,
│                #               supervisor, lock, heartbeat, prune, backup, replay
└── cli/         # Typer entry points — main, lifecycle, ops, inspect, dev
```

Layering is enforced by ruff TID banned-imports: `core` ↑ `infra` ↑
`triggers`/`handlers` ↑ `app` ↑ `cli`. A cross-layer import that violates
this fails lint.

---

## Useful commands

```bash
# Health
just doctor                # state_dir / disk / heartbeat / db / token / pause
just status                # outbox depths + quarantined triggers + pidfile

# Operations
just backup                # hot SQLite snapshot under <state_dir>/backups/
just prune                 # apply retention (events 90d, runs 30d, dedup ttl, …)
hyejin-bot ops replay <event_id> --confirm
hyejin-bot lifecycle pause | resume

# Inspection
hyejin-bot inspect status                 # outbox depths + in-flight + quarantine
hyejin-bot inspect events ls              # recent events
hyejin-bot inspect events get <event_id>  # full event + outbox/runs history
hyejin-bot inspect handlers ls            # registered handlers + manifests
hyejin-bot inspect pr-review              # last PR-review attempts (audit table)
hyejin-bot inspect ratelimit              # token-bucket state for each bucket
```

## Exit codes wrappers care about

| Code | Name        | Meaning                                       | Auto-restart |
|------|-------------|-----------------------------------------------|--------------|
| 0    |             | clean shutdown                                | yes          |
| 75   | EX_TEMPFAIL | another instance holds the pidfile lock       | yes          |
| 78   | EX_CONFIG   | `AuthError` / `ConfigError` — operator action | **no**       |

## License

Proprietary, internal-use-only. Not for redistribution.
