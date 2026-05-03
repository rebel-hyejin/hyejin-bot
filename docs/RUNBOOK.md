# Operations Runbook

Single-operator daemon (`daeyeon-bot`). State directory defaults to
`~/.daeyeon-bot/`. Read `docs/PLAN.md` for design and `CONTRACTS.md` for
delivery semantics.

The first half is **routine ops**; the second half is **incident playbooks**.
Every command assumes the operator's shell on the host running the daemon.

---

## 1. Routine ops

### Health check
```bash
just doctor                  # daeyeon-bot ops doctor
just status                  # daeyeon-bot inspect status
```
`ops doctor` exits non-zero if any check is `fail`. Status shows pidfile
owner, heartbeat age, outbox depth, and PAUSE flag.

### Pause / resume
```bash
touch ~/.daeyeon-bot/PAUSE   # blocks new Claude calls; in-flight finishes
rm   ~/.daeyeon-bot/PAUSE
```

### Apply schema migrations
```bash
just migrate                 # idempotent; safe to run on a live host
```

### Hot SQLite snapshot
```bash
just backup                  # Connection.backup() → state-<UTC>.db, 0600
```
Snapshots land under `~/.daeyeon-bot/backups/` and are pruned to
`retention.backup_keep` (config default: 5). Safe while the daemon runs.

### Apply retention
```bash
just prune
```
Deletes old runs, expired dedup keys, and old events whose outbox rows
are all settled. Active outbox traffic is never touched.

### Replay a dead-lettered event
```bash
daeyeon-bot ops replay <event_id>             # dry-run
daeyeon-bot ops replay <event_id> --confirm   # bumps attempt_epoch
```

---

## 2. Mac / Linux parity

| Concern              | macOS (launchd)                                   | Linux (systemd --user)                          |
|----------------------|---------------------------------------------------|-------------------------------------------------|
| Install              | `just install-mac`                                | `just install-linux <oauth-credential-file>`    |
| Unit / plist         | `~/Library/LaunchAgents/ai.rebellions.daeyeon-bot.plist` | `~/.config/systemd/user/daeyeon-bot.service` |
| Auto-restart         | `KeepAlive=true`, `ThrottleInterval=10`           | `Restart=on-failure`, `RestartSec=10`           |
| Stop on AuthError    | `RestartPreventExitStatus=78` (via wrapper)       | `RestartPreventExitStatus=78`                   |
| Watchdog             | `ops doctor` cron / heartbeat self-alert log      | `WatchdogSec=120` + sd_notify                   |
| Token storage        | macOS Keychain (`security add-generic-password`)  | `0600` file under `~/.config/`, `LoadCredential=` |
| Logs                 | `StandardOutPath` / `StandardErrorPath` files     | `journalctl --user -u daeyeon-bot`              |
| Bootstrap token      | `just setup-token` (Keychain prompt)              | Manual: write file with `umask 077`             |
| Manual restart       | `launchctl kickstart -k gui/<uid>/ai.rebellions.daeyeon-bot` | `systemctl --user restart daeyeon-bot`  |

Exit codes that matter:
- **75 (`EX_TEMPFAIL`)** — another instance already holds the pidfile lock.
  Wrappers should retry.
- **78 (`EX_CONFIG`)** — `AuthError` or `ConfigError`. **Do not auto-restart.**
  Operator must intervene (rotate token, fix config).

---

## 3. Incident playbooks

### 3.1 Corrupt `state.db`

**Symptoms**
- `ops doctor` reports `db_integrity: fail` (`PRAGMA integrity_check` returns anything but `ok`).
- Daemon crashes on boot with `aiosqlite.DatabaseError: database disk image is malformed`.
- `journalctl` / launchd stderr shows repeated restart loop.

**Recovery**
1. **Stop the daemon.**
   ```bash
   # macOS
   launchctl unload ~/Library/LaunchAgents/ai.rebellions.daeyeon-bot.plist
   # Linux
   systemctl --user stop daeyeon-bot
   ```
2. **Move the bad DB aside** (do **not** delete; we may dump rows from it).
   ```bash
   cd ~/.daeyeon-bot
   mv state.db state.db.corrupt-$(date -u +%Y%m%dT%H%M%SZ)
   mv state.db-wal state.db-wal.corrupt 2>/dev/null
   mv state.db-shm state.db-shm.corrupt 2>/dev/null
   ```
3. **Restore the latest backup.**
   ```bash
   ls -1t backups/state-*.db | head -5
   cp backups/state-<latest>.db state.db
   chmod 600 state.db
   ```
4. **Run integrity check + migrate forward.**
   ```bash
   sqlite3 state.db 'PRAGMA integrity_check;'   # must print "ok"
   just migrate                                 # idempotent
   just doctor                                  # all green
   ```
5. **Restart the daemon and verify.**
   ```bash
   # macOS
   launchctl load -w ~/Library/LaunchAgents/ai.rebellions.daeyeon-bot.plist
   # Linux
   systemctl --user start daeyeon-bot
   just status
   ```
6. **Salvage what the backup missed.**
   Use `sqlite3 state.db.corrupt-<stamp> '.recover'` (or `.dump`) to extract
   any rows newer than the backup; manually re-emit important events with
   `daeyeon-bot ops replay <event_id> --confirm`.

**Postmortem checklist**
- [ ] Disk SMART status (`smartctl -a`) — corruption often = failing media.
- [ ] Confirm `journal_mode=WAL` and `synchronous=NORMAL` are still set
      (they are forced in `infra/storage.py:open_db`; only an external tool
      could have changed them).
- [ ] Bump `retention.backup_keep` if the latest backup was too old.

---

### 3.2 OAuth token revoked / `AuthError` restart loop

**Symptoms**
- `ops doctor` `token` check is `fail` (`unavailable`).
- Daemon exits with code **78**; supervisor refuses to restart
  (`RestartPreventExitStatus=78`).
- Logs show `AuthError: claude auth failure: …` or `401/403/unauthorized`
  responses from the Claude SDK.

**Recovery**
1. **Verify the failure mode** before rotating anything.
   ```bash
   just doctor                          # token: fail / unavailable?
   ```
2. **Issue a fresh token** through the Claude CLI on the operator's laptop:
   ```bash
   claude setup-token                   # opens browser, prints token
   ```
3. **Store the new token in the right provider.**
   - **macOS:**
     ```bash
     just setup-token                   # prompts, writes to Keychain
     ```
   - **Linux:** write the credential file with `umask 077` and re-run the
     installer so systemd's `LoadCredential=` picks it up.
     ```bash
     umask 077
     printf '%s' "<token>" > ~/.config/daeyeon-bot/oauth_token
     just install-linux ~/.config/daeyeon-bot/oauth_token
     ```
4. **Verify and restart.**
   ```bash
   just doctor                          # token: ok, len>0
   # macOS
   launchctl kickstart -k gui/$(id -u)/ai.rebellions.daeyeon-bot
   # Linux
   systemctl --user restart daeyeon-bot
   just status
   ```
5. **Confirm the daemon makes a real Claude call.**
   ```bash
   daeyeon-bot dev emit-manual hello-world      # or your smoke trigger
   ```

**Postmortem checklist**
- [ ] Token expiry date noted? (rotate before next expiry).
- [ ] Was the old token committed or logged anywhere? `just check` and
      `git log -p` for `sk-ant-` / `oat`. The redact processor scrubs new
      writes, but historical leaks need a separate cleanup.
- [ ] If revocation was due to a leak, also rotate API-key style tokens
      held by other services on the same host.

---

### 3.3 Hung daemon / heartbeat lag

**Symptoms**
- `journalctl` (Linux) or launchd stderr shows
  `heartbeat.tick_lag elapsed_s=… threshold_s=…`.
- `just status` heartbeat age > tick × 3 (default 90 s).
- systemd watchdog killed the process (Linux only — `WatchdogSec=120`).

**Recovery**
1. **Capture state before restarting** so we can debug the stall.
   ```bash
   ps -p $(cat ~/.daeyeon-bot/daeyeon-bot.pid) -o pid,etime,%cpu,%mem,stat,wchan,comm
   # Linux
   journalctl --user -u daeyeon-bot --since '15 min ago' > /tmp/db-stall.log
   # macOS
   tail -n 500 ~/.daeyeon-bot/stderr.log > /tmp/db-stall.log
   ```
2. **Restart cleanly** (the supervisor will already have done this on Linux
   after the watchdog fired):
   ```bash
   # macOS
   launchctl kickstart -k gui/$(id -u)/ai.rebellions.daeyeon-bot
   # Linux — only if it isn't already running
   systemctl --user restart daeyeon-bot
   ```
3. **Confirm recovery picked up `interrupted` rows.** Boot logs include
   `outbox.recovered status=…` lines; `inspect status` outbox depths should
   converge.

**Postmortem checklist**
- [ ] Was disk pressure / fsync the cause? (`iostat -x 1` during the stall.)
- [ ] Was the host suspended (laptop closed)? launchd cannot prevent that;
      consider running on the always-on Linux server instead.
- [ ] If the stall recurs, lower `tick_s` or wire `WatchdogSec` more aggressively.

---

### 3.4 Pidfile lock conflict (exit 75)

**Symptoms**
- `daeyeon-bot run` exits with code **75** immediately.
- `~/.daeyeon-bot/daeyeon-bot.pid` exists and points to a live PID.

**Recovery**
1. **Check who owns the lock.**
   ```bash
   pid=$(cat ~/.daeyeon-bot/daeyeon-bot.pid)
   ps -p "$pid" -o pid,user,etime,comm
   ```
2. If the PID is the actual running daemon — that is the correct behaviour;
   stop trying to launch a second instance.
3. If the PID is **stale** (process gone, pidfile left behind):
   ```bash
   rm ~/.daeyeon-bot/daeyeon-bot.pid
   just run
   ```
   `app/lock.py` only removes its own pidfile on graceful shutdown, so a
   `kill -9` or power loss can leave a stale file. The flock advisory lock
   itself is released by the kernel on exit, so deletion is safe.

---

### 3.5 Disk full / SQLite "database or disk is full"

**Symptoms**
- `outbox.commit` raises `sqlite3.OperationalError: database or disk is full`.
- `df -h ~` shows the home volume at 100 %.

**Recovery**
1. **Reclaim space outside `state_dir` first** (logs, caches, derived data).
2. **Run retention** to shrink the DB:
   ```bash
   just prune
   sqlite3 ~/.daeyeon-bot/state.db 'PRAGMA wal_checkpoint(TRUNCATE);'
   sqlite3 ~/.daeyeon-bot/state.db 'VACUUM;'    # daemon must be stopped
   ```
3. **Reduce `retention.backup_keep`** in `config.toml` if backups dominate.
4. **Restart and verify** with `just doctor` + `just status`.

---

## 4. When in doubt

- `daeyeon-bot ops doctor` is the single best diagnostic.
- `journalctl --user -u daeyeon-bot -f` (Linux) or `tail -f
  ~/.daeyeon-bot/stderr.log` (macOS) for live structlog stream.
- `daeyeon-bot inspect status` for outbox / heartbeat / PAUSE / pidfile.
- `daeyeon-bot inspect outbox --status dead_letter` to see what needs replay.

This daemon serves one operator on one host. Restart freely; replay
manually; rotate the token when it leaks. The boring playbook is the
right playbook.
