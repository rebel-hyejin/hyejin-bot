# DEPLOY.md — Fresh-machine operator guide

> Audience: the operator (hyejin.han@rebellions.ai) deploying hyejin-bot
> to a new host. The bot is single-tenant + single-process; one daemon
> per host. This document is the procedure: prerequisites → token →
> install → smoke test → daily cheatsheet → upgrade → uninstall.
>
> Mac uses `launchd` + Keychain. Linux uses `systemd --user` + a 0600 file.
> Both are the same daemon — only the supervisor and the secrets provider
> differ.

---

## 0. Decide before you start

| Question | Answer to write down |
|---|---|
| Mac (laptop) or Linux server? | _________ |
| What's the operator UNIX user? | (e.g. `hyejin.han` — `whoami`) |
| What's `$HOME`? | (e.g. `/home/ldap/hyejin.han`) |
| Where will state live? | default `~/.hyejin-bot/`; override with `[runtime].state_dir` |
| Which GitHub user will the bot review as? | (your operator account) |
| Which Claude account holds the Pro/Max subscription? | (the one paying for the OAuth token) |

If `gh auth login` and the daemon will run under different users on the
same host, **do not deploy.** The handler shells out to `gh` from the
daemon's user; that user must own the gh credential cache.

---

## 1. Prerequisites

Install once per machine. Versions are minimums.

### 1.1 Mac

```bash
# Homebrew packages
brew install uv gh jq sqlite git

# Claude Code CLI (used for `claude setup-token` only — the daemon uses
# the SDK directly via uv).
brew install --cask claude  # or follow https://claude.com/claude-code

# Verify
uv --version            # ≥ 0.5
gh --version            # ≥ 2.40
jq --version            # ≥ 1.6
python3 --version       # 3.12.x — uv will install/manage 3.12 itself if absent
sqlite3 --version       # any (used for ad-hoc DLQ inspection)
```

### 1.2 Linux (server)

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install -y curl git jq sqlite3 ca-certificates

# uv (single-binary installer — ends up at ~/.local/bin/uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
. ~/.bashrc

# gh CLI (Debian/Ubuntu instructions from cli.github.com)
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
  sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | \
  sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
sudo apt update && sudo apt install -y gh

# systemd --user must be enabled at boot for headless servers, otherwise
# the unit only runs while you're logged in.
sudo loginctl enable-linger "$(whoami)"

# Claude Code CLI — needed only to mint the OAuth token (one-shot).
# Easiest: install on a Mac, mint the token, then copy the token to the
# Linux box. If you must do it on the server, follow the official docs.

# Verify
uv --version && gh --version && jq --version && sqlite3 --version
```

### 1.3 Repo

```bash
mkdir -p ~/workspace && cd ~/workspace
git clone git@github.com:rebellions-sw/hyejin-bot.git
cd hyejin-bot
just sync                                # uv pulls deps + pins Python 3.12
just check                               # lint + typecheck + tests must all pass
```

A clean `just check` is the gate. If it fails, **stop** — do not deploy a
broken build.

---

## 2. Configuration

```bash
cp config.example.toml config.toml       # gitignored
```

Edit `config.toml`. The defaults work for most boxes, but verify:

| Key | What it controls | Action |
|---|---|---|
| `[runtime].state_dir` | Where state.db / pidfile / heartbeat / backups live. | Default `~/.hyejin-bot`. Change only if `~` is networked / slow / shared. |
| `[secrets].provider` | `vault` (server, Recommended) \| `keychain` (Mac dev) \| `file` \| `env` (dev only). | Set per OS. |
| `[secrets].keychain_account` | Mac-only: Keychain account name for the Anthropic key. | Default `claude_api_key`. |
| `[secrets].file_path` | Linux `file` provider: path to the 0600 credential file. | Default `/etc/hyejin-bot/claude_api_key`. Only used when `provider="file"`; the Vault path is the prod choice. |
| `[secrets].vault_*` | Vault AppRole + KV settings. | See `config.example.toml` `[secrets]` block. Defaults wire to `secret/bots/hyejin-bot` via `~/bots/.vault/hyejin-bot.{role,secret}_id`. |
| `[claude].model` | Which model the daemon calls. | Leave at `claude-opus-4-7` unless you have a reason. |
| `[github].username` | Operator's GitHub login. | Set explicitly (avoids a network roundtrip at boot). Find with `gh api user -q .login`. |
| `[handlers.pr_review].enabled` | Master switch for PR review. | `true` to ship. `false` if you want to deploy the daemon first and enable later. |
| `[handlers.pr_review].persona_skill` | Which directory under `<skills_root>/` contains `SKILL.md`. | Default `hyejin-bot-code-review` (ships with the repo). |
| `[handlers.pr_review].skills_root` | Where to look up the persona. | Commented-out by default → uses repo-bundled `.claude/skills/`. Set to `~/.claude/skills` if you want to edit the persona without touching the repo. |
| `[handlers.pr_review.size_budget]` | Per-PR diff cap. | `max_lines=1000`, `max_files=50`. Bigger PRs are skipped unless `--force`. |
| `[handlers.pr_review].allowed_repos` | Security boundary — `fnmatch` globs over `owner/name`. | Empty list (default) = no filter (legacy). Once set, blocked PRs land as `skipped_disallowed_repo`; `--force` does **not** bypass. |
| `[ratelimit]` | Token bucket the dispatcher consults before every claim. | `claude_call_capacity = 60.0`, `claude_call_refill_per_sec = 1.0` — soft 60/min cap with full burst. Migration 003 seeds these. |
| `[routing]` | event.type → list of handler names. | Stock entries cover the built-ins. Don't remove them unless you know why. |

`config.toml` is **not committed**. Keep it under your dotfiles or a
private repo if you want versioned configs across machines.

---

## 3. Secrets — Vault provider (production) or Keychain (Mac dev)

The daemon never reads its shell environment for the Anthropic key or
ancillary secrets (GH_TOKEN, SLACK_BOT_TOKEN, JIRA_*, SSW_AUTOMATION_PASSWORD).
It pulls from the configured `secrets.provider` at boot:

- **Linux server** → `vault` (HashiCorp Vault AppRole + KV v2). Used in
  the production VM rollout — `secret/bots/hyejin-bot` holds every key
  the daemon needs in one path.
- **Mac dev** → `keychain` (the macOS login keychain) is OK when you
  don't want to provision Vault credentials for a one-machine install.
- **`file`** (0600 file on disk) and **`env`** remain available but
  are not recommended for hyejin-bot — Vault wins on rotation hygiene.

The Anthropic auth itself is the **Claude Code org-OAuth credentials
file** (`~/.claude/.credentials.json`), not an API key. The persona's
prompts run through the bundled `claude` CLI, which finds those
credentials via `$HOME` automatically. The Vault key
`ANTHROPIC_API_KEY` is optional — leave it empty and the daemon uses
the credentials file instead (see `infra/claude.py:RealClaudeSession`).

### 3.1 Vault path layout (server)

```text
secret/bots/hyejin-bot                     # KV v2, owned by hyejin-bot-ro policy
  ├── ANTHROPIC_API_KEY = ""               # empty → OAuth credentials path
  ├── GH_TOKEN          = ghp_…            # fine-grained PAT, rebellions-sw scope
  ├── GH_USER           = rebel-hyejin
  ├── REPO              = rebellions-sw/ssw-bundle
  ├── SLACK_BOT_TOKEN   = xoxb-…           # for LGTM-eligible DM
  └── SLACK_CHANNEL     = D08GP012483      # operator DM id
```

Vault policy `hyejin-bot-ro` covers read on the KV path + token
self-revoke (`scripts/bootstrap-vault-approle.sh` documents the
admin-side bootstrap).

### 3.2 AppRole credentials (server)

`scripts/install-linux.sh` refuses to install without two 0600 files:

```text
~/bots/.vault/hyejin-bot.role_id
~/bots/.vault/hyejin-bot.secret_id
```

Mint them with the helper:

```bash
./scripts/bootstrap-vault-approle.sh           # writes both files with chmod 600
ls -la ~/bots/.vault/
```

Pre-flight (`bootstrap-vault-approle.sh`):
- requires `vault login` already done on the operator's session,
- pulls the `role_id` (constant per role) and a fresh `secret_id`,
- never echoes either secret to the terminal.

`config.toml` already points at this layout — see the `[secrets]`
block in `config.example.toml` for the exact field names
(`vault_role_id_path`, `vault_secret_id_path`, …).

### 3.3 OAuth credentials for the persona (server)

Copy your Mac Keychain `Claude Code-credentials` blob (~470 bytes JSON
with `claudeAiOauth.{accessToken, refreshToken, expiresAt, scopes,
subscriptionType, rateLimitTier}`) to the VM:

```bash
# On Mac:
security find-generic-password -s "Claude Code-credentials" -w \
    > /tmp/creds.json && chmod 600 /tmp/creds.json
scp /tmp/creds.json hyejin-vm:~/.claude/.credentials.json
ssh hyejin-vm 'chmod 600 ~/.claude/.credentials.json; rm -f /tmp/creds.json'
rm -f /tmp/creds.json
```

The CLI refreshes the access token in-place when it expires (refreshToken
must be present — first install often ships with an empty refreshToken
which leads to a 24h-later 401; re-copy from Keychain to refill).

### 3.4 Mac (dev) — Keychain fallback

If you're running a single dev daemon on Mac and don't want to
provision Vault credentials just for that:

```bash
just setup-token            # script prompts for the Anthropic API key
                            # and stores it under (hyejin-bot, claude_api_key).
security find-generic-password -s hyejin-bot -a claude_api_key -w
```

The Mac install reads `config.toml`'s `[secrets].provider = "keychain"`
+ `keychain_account = "claude_api_key"`. Same set of named secrets
(GH_TOKEN, SLACK_BOT_TOKEN, …) goes into the Keychain under the same
service name but with their own account names.

> **Never** put any of these in `.env`, in `git`, or in
> `EnvironmentVariables` of the launchd plist. The daemon scrubs known
> token shapes from logs but the redaction processor only protects what
> happens to land in structlog — environment variables can leak via
> `ps`, `proc`, and crash dumps.

---

## 4. GitHub auth (PR-review only)

The PR-review handler shells out to `gh` for diff reads, comment posts,
and review submission. Authenticate as the operator:

```bash
gh auth login --hostname github.com --git-protocol https \
    --scopes "repo,read:org" --web
gh auth status
gh api user -q .login                    # confirms the account
```

Required scopes:

- `repo` — read/write PR comments, submit reviews.
- `read:org` — list members for self-authored detection.

If your org enforces SSO, you must `gh auth refresh -h github.com -s repo,read:org`
once per browser session and click "Authorize" on the SSO page. The token
gets cached in `gh`'s credential store; the daemon does not see it directly.

---

## 5. Persona (PR-review only)

`SKILL.md` is the prompt the bot uses to review PRs. It's loaded fresh
on every event by mtime, so editing it does NOT require a restart.

### 5.1 Repo-bundled (default)

`.claude/skills/hyejin-bot-code-review/SKILL.md` ships with the repo
and is what the default config uses. Don't edit it casually — it's
checked in. To customise, copy it to your home skills dir (next).

### 5.2 Home skills dir

```bash
mkdir -p ~/.claude/skills
cp -r .claude/skills/hyejin-bot-code-review ~/.claude/skills/
# Then in config.toml:
#   [handlers.pr_review]
#   skills_root = "~/.claude/skills"
#   persona_skill = "hyejin-bot-code-review"
```

Now `~/.claude/skills/hyejin-bot-code-review/SKILL.md` is your private
override. The daemon picks up edits on the next event without a restart.

The handler refuses to run if `SKILL.md` is missing, unreadable, or
shorter than `min_persona_chars` (default 200). Failures land as
`pr_review_audit.status='failed'` with `error='persona unavailable: …'`.

---

## 6. Initialise the SQLite store

```bash
just migrate
sqlite3 ~/.hyejin-bot/state.db "SELECT value FROM meta WHERE key='schema_version';"
# → expect '4' on this revision (001 init, 002 PR review, 003 ratelimit
#   seed, 004 skipped_disallowed_repo audit status).
```

State directory layout after first boot:

```
~/.hyejin-bot/
├── state.db          # primary store (WAL)
├── state.db-wal
├── state.db-shm
├── hyejin-bot.pid   # pidfile + flock (single-instance enforcement)
├── heartbeat         # mtime touched every tick — liveness signal
├── PAUSE             # absent by default; presence blocks Claude calls
├── backups/          # state-<UTC>.db snapshots from `just backup`
└── launchd.{out,err}.log   # Mac only
```

---

## 7. Smoke test (still in foreground)

```bash
just doctor
# Expect: secrets ✓, db ✓, pause ✓, heartbeat ✓ (or "not yet" pre-boot).
```

Boot the daemon in foreground:

```bash
just run
# Watch for: boot.start, then one heartbeat.tick line every interval.
# Ctrl-C to stop.
```

In another terminal — fire one manual event end to end:

```bash
hyejin-bot dev fire manual -m 'hello'
hyejin-bot inspect events ls --n 5
hyejin-bot inspect tail --n 5           # echo handler should show acked
```

PR-review dry run (does NOT write to GitHub):

```bash
hyejin-bot dev fire-pr-review --pr 'rebellions-sw/some-repo#42' --dry-run
# Prints the event JSON + the routing target. Confirms gh + config wiring.
```

PR-review live (re-runs at the same SHA, appends a "Supersedes review"
header):

```bash
hyejin-bot dev fire-pr-review --pr 'rebellions-sw/some-repo#42' --force
# Then watch the next heartbeat — the dispatcher claims the row.
hyejin-bot inspect pr-review --pr 'rebellions-sw/some-repo#42'
# Should show status=posted with a review_id.
```

If everything's green, stop the foreground daemon and continue.

---

## 8. Install as a daemon

### 8.1 Mac — launchd user agent

```bash
just install-mac
launchctl list | grep hyejin-bot        # PID present → alive
tail -f ~/.hyejin-bot/launchd.err.log
```

The plist sets `KeepAlive=true` (auto-restart on crash) and
`ThrottleInterval=10s` so an `AuthError` (exit 78) loop is rate-limited.
Logs are at `~/.hyejin-bot/launchd.{out,err}.log`.

To re-install after a config change: `just install-mac` again — the
script unloads the old plist before loading the new one.

### 8.2 Linux — systemd user unit

```bash
bash scripts/install-linux.sh           # no args — secrets come from Vault
systemctl --user status hyejin-bot
journalctl --user -u hyejin-bot -f
```

Pre-flight (script):
- `~/bots/.vault/hyejin-bot.{role_id,secret_id}` exist with mode 0600
  (from `scripts/bootstrap-vault-approle.sh`),
- `config.toml` exists in the repo root,
- `~/.hyejin-bot/` exists with mode 0700 (created by the script).

The unit is `Type=notify` with `WatchdogSec=120` — our heartbeat task
calls `sd_notify(WATCHDOG=1)` every tick. If the daemon hangs, systemd
SIGKILLs and restarts. `RestartPreventExitStatus=78` blocks restart on
`AuthError`.

No `LoadCredential` — the daemon reads its Anthropic credentials from
`$HOME/.claude/.credentials.json` (org-OAuth, copied in §3.3) and pulls
GH/Slack/Jira tokens from Vault at boot via AppRole. `ProtectHome=read-only`
on the unit still allows the daemon to read those files in-process; only
the state dir is writable.

To re-install after a config change: `bash scripts/install-linux.sh` again.
Daemon-reload + restart happens automatically.

If the unit only runs when you're logged in, you forgot
`loginctl enable-linger`. Re-run that and reboot.

---

## 9. Verify it's actually working

```bash
# Liveness (any host)
just doctor                      # all ✓?
just status                      # outbox depths + quarantined triggers
ls -l ~/.hyejin-bot/heartbeat   # mtime should be < 60s old

# Mac
launchctl list | grep hyejin-bot       # PID + last exit status
tail -f ~/.hyejin-bot/launchd.err.log

# Linux
systemctl --user is-active hyejin-bot
journalctl --user -u hyejin-bot -n 100 --no-pager
journalctl --user -u hyejin-bot -p err -n 50  # errors only
```

Smoke test the PR-review path on a real PR you control:

```bash
gh pr create -R you/test-repo --base main --title 'smoke' --body 'smoke'
# Add the operator account as a reviewer (web UI or gh CLI).
# The polling trigger picks it up within one poll_interval_seconds (default 300s).
hyejin-bot inspect pr-review --n 10   # newest first; expect status=posted
```

If `status=posted` appears, you're done.

---

## 10. Daily cheatsheet

```bash
# Health
just doctor
just status
sqlite3 ~/.hyejin-bot/state.db \
  "SELECT status, COUNT(*) FROM outbox GROUP BY status;"

# Inspection
hyejin-bot inspect events ls --n 20
hyejin-bot inspect events get <event_id>
hyejin-bot inspect tail --n 20
hyejin-bot inspect handlers ls
hyejin-bot inspect pr-review --n 20
hyejin-bot inspect pr-review --pr 'owner/repo#N'
hyejin-bot inspect ratelimit         # token-bucket state per bucket

# Operations
hyejin-bot lifecycle pause          # touches PAUSE — blocks Claude calls
hyejin-bot lifecycle resume         # removes PAUSE
just backup                           # hot snapshot under <state_dir>/backups/
just prune                            # apply retention defaults

# Replay a dead-lettered event
sqlite3 ~/.hyejin-bot/state.db \
  "SELECT event_id,handler,err FROM outbox WHERE status='dead_letter';"
hyejin-bot ops replay <event_id> --handler pr_review --confirm

# Manual PR review (re-request from operator, supersedes prior review)
hyejin-bot dev fire-pr-review --pr 'owner/repo#N' --force
```

---

## 11. Configuration reference (cheat-card)

`config.example.toml` is the source of truth for every knob; this is
the operator-facing summary.

| Section | Key | Default | Purpose |
|---|---|---|---|
| `[runtime]` | `state_dir` | `~/.hyejin-bot` | All runtime files. |
| `[runtime]` | `shutdown_budget_seconds` | `180` | Phase A+B+C total. |
| `[logging]` | `level` | `INFO` | structlog level. |
| `[logging]` | `format` | `json` | `json` for prod, `console` for dev. |
| `[retention]` | `events_days` | `90` | Events table prune horizon. |
| `[retention]` | `runs_days` | `30` | Runs table prune horizon. |
| `[retention]` | `runs_keep_per_handler` | `10` | Floor; never prune below this per handler. |
| `[retention]` | `dedup_default_ttl_days` | `7` | Default dedup-key TTL. |
| `[retention]` | `backup_keep` | `5` | Snapshots kept under `backups/`. |
| `[retention]` | `gh_state_dormant_days` | `90` | Withdrawn `gh_review_requested_state` rows pruned after this. |
| `[ratelimit]` | `claude_call_capacity` | `60.0` | Burst budget for the active token bucket. |
| `[ratelimit]` | `claude_call_refill_per_sec` | `1.0` | Steady-state refill rate (tokens / second). |
| `[ratelimit.defaults]` | `global_per_hour` / `global_per_day` / `handler_per_hour` | `30` / `200` / `10` | Legacy aggregate caps; retained for forward compat. The active gate is `[ratelimit]` above. |
| `[secrets]` | `provider` | `keychain` | `vault` (prod) \| `keychain` (Mac dev) \| `file` \| `env`. |
| `[secrets]` | `keychain_service` / `_account` | `hyejin-bot` / `claude_api_key` | Keychain coords (Mac dev). |
| `[secrets]` | `file_path` | `/etc/hyejin-bot/claude_api_key` | Linux 0600 file path (`file` provider). |
| `[secrets]` | `vault_addr` | `https://vault.ssw.rbln.in` | Vault server (`vault` provider). |
| `[secrets]` | `vault_kv_path` | `bots/hyejin-bot` | KV v2 path holding ANTHROPIC_API_KEY / GH_TOKEN / SLACK_BOT_TOKEN. |
| `[secrets]` | `vault_role_id_path` / `vault_secret_id_path` | `~/bots/.vault/hyejin-bot.{role_id,secret_id}` | 0600 files written by `bootstrap-vault-approle.sh`. |
| `[slack]` | `enabled` / `channel` | `false` / _(empty)_ | LGTM-eligible DM side channel. Set both to opt in. |
| `[claude]` | `model` | `claude-opus-4-7` | Model the SDK uses. |
| `[claude]` | `default_system_prompt` | `"You are…"` | Used if a handler doesn't override. |
| `[github]` | `username` | _(empty)_ | Resolved at boot if blank. |
| `[github]` | `gh_call_timeout_seconds` | `30` | Per-`gh` subprocess timeout. |
| `[triggers.manual]` | `enabled` | `true` | CLI-fired only. |
| `[triggers.gh_review_requested]` | `enabled` | `true` | Polling trigger. |
| `[triggers.gh_review_requested]` | `poll_interval_seconds` | `300` | How often to call `gh search`. |
| `[handlers.pr_review]` | `enabled` | `true` | Master switch. |
| `[handlers.pr_review]` | `persona_skill` | `hyejin-bot-code-review` | Skill directory name. |
| `[handlers.pr_review]` | `skills_root` | _(commented)_ | Override location. |
| `[handlers.pr_review]` | `min_persona_chars` | `200` | Below this → persona invalid. |
| `[handlers.pr_review]` | `concurrency` | `1` | Bump to `2` to overlap `gh` prep with Claude wait when batching PRs (doubles `gh` traffic). |
| `[handlers.pr_review]` | `allowed_repos` | `[]` | Security allowlist of `fnmatch` globs over `owner/name`. Empty = no filter. |
| `[handlers.pr_review.size_budget]` | `max_lines` | `1000` | Per-PR diff cap. |
| `[handlers.pr_review.size_budget]` | `max_files` | `50` | Per-PR file-count cap. |

Env overrides use `DAEYEON_BOT__SECTION__KEY=…`. Example:
`DAEYEON_BOT__LOGGING__LEVEL=DEBUG just run`.

---

## 12. Upgrade

```bash
cd ~/workspace/hyejin-bot
git fetch && git status                  # confirm clean
git pull --ff-only                       # never force-pull
just sync                                # uv refreshes deps if needed
just check                               # lint + typecheck + tests must pass
just migrate                             # apply any new migrations

# Mac
just install-mac                         # reloads the plist

# Linux
bash scripts/install-linux.sh
systemctl --user restart hyejin-bot
journalctl --user -u hyejin-bot -f
```

If `just check` fails on the new revision, **do not** restart the
daemon — fix or roll back first (`git reset --hard <prev>` is OK on a
deployment checkout you don't push from).

---

## 13. Uninstall

### 13.1 Mac

```bash
launchctl unload ~/Library/LaunchAgents/ai.rebellions.hyejin-bot.plist
rm ~/Library/LaunchAgents/ai.rebellions.hyejin-bot.plist
security delete-generic-password -s hyejin-bot -a claude_api_key
# (and one per named secret you stored — gh_token, slack_bot_token, etc.)
rm -rf ~/.hyejin-bot                     # only if you really want to drop state
```

### 13.2 Linux

```bash
systemctl --user disable --now hyejin-bot
rm ~/.config/systemd/user/hyejin-bot.service
systemctl --user daemon-reload
shred -u ~/bots/.vault/hyejin-bot.role_id ~/bots/.vault/hyejin-bot.secret_id
shred -u ~/.claude/.credentials.json
rm -rf ~/.hyejin-bot                         # only if you really want to drop state
```

You may also want to (a) revoke the Vault AppRole secret_id
(`vault write -f auth/approle/role/hyejin-bot/secret-id-accessor/destroy`),
(b) revoke the GitHub PAT at `github.com/settings/tokens`, and
(c) sign out the OAuth subscription at `claude.com/settings` if this
host is being permanently decommissioned.

---

## 14. When something looks wrong

`docs/RUNBOOK.md` is the operations manual. Five incident playbooks live
there: corrupt SQLite, token revocation, hung daemon, lock conflict,
disk full — plus the PR-review-specific recovery flows
(`persona unavailable`, `gh auth status` broken, raising the size budget,
inspecting the audit log).

For the daemon's design and why each piece works the way it does:
`docs/PLAN.md`. For the stable contracts (delivery semantics,
HandlerResult, manifests): `CONTRACTS.md`. For code-level guardrails
when editing: `CLAUDE.md`.
