# Quickstart — GitHub PR Review Automation Bot

For an operator who already has the hyejin-bot daemon running (Phases 0–6
complete) and wants to enable PR-review auto-review.

---

## 0. Prerequisites

```bash
# 1. The daemon is installed and the basic loop works.
just doctor                       # should pass
just status                       # should show the dispatcher running

# 2. The `gh` CLI is installed and authenticated to github.com.
gh --version                      # any recent version
gh auth status                    # MUST show "Logged in to github.com as <you>"
```

If `gh auth status` is not green, run `gh auth login` first. The bot does
not maintain its own GitHub credentials — it asks `gh` for them at boot.

---

## 1. Author your review persona

```bash
mkdir -p ~/.claude/skills/pr-review
$EDITOR ~/.claude/skills/pr-review/SKILL.md
```

Minimum content (≥ 200 chars after frontmatter strip — the bot enforces
this):

```markdown
---
name: pr-review
description: Default PR review persona for hyejin-bot.
---

You are reviewing GitHub pull requests on Daeyeon's behalf. Be direct,
specific, and skip nitpicks. Quote the line you're commenting on. If the
change is fine, say so plainly in the Summary.

Priorities (in order): correctness > maintainability > tests > conventions.
```

Edit it whenever you want — the bot reads it fresh on each review (no
restart needed). See `contracts/persona-skill-format.md` for the full
contract and a richer example.

---

## 2. Wire it into config

Edit `~/.hyejin-bot/config.toml` (or wherever `DAEYEON_BOT_CONFIG` points):

```toml
[github]
# Optional. If empty, the bot resolves it via `gh api user` at boot.
username = ""
gh_call_timeout_seconds = 30

[triggers.gh_review_requested]
enabled = true
poll_interval_seconds = 300         # 5 min — spec default

[handlers.pr_review]
enabled = true
idempotent = true
dedup_ttl_seconds = 86400
concurrency = 1
accepts = ["gh.review_requested", "pr.review.manual"]
persona_skill = "pr-review"          # the directory name from step 1
min_persona_chars = 200

[handlers.pr_review.size_budget]
max_lines = 1000
max_files = 50

[routing]
# Add these two lines without removing existing ones:
"gh.review_requested" = ["pr_review"]
"pr.review.manual"    = ["pr_review"]
```

Apply migration #2 (adds `gh_review_requested_state` and `pr_review_audit`
tables) and reload config:

```bash
just migrate                       # idempotent; brings schema_version to 2
just doctor                        # should now show schema_version=2
# Restart the daemon to pick up the new config + trigger:
launchctl kickstart -k gui/$UID/com.hyejin.bot   # macOS
# or:  systemctl --user restart hyejin-bot       # Linux
```

---

## 3. Smoke-test with a manual review

Pick a small PR (under 50 files, under 1000 changed lines) you have access
to. Then:

```bash
# Dry run — does NOT post:
uv run hyejin-bot dev fire pr-review --pr "owner/repo#42" --dry-run

# Real post:
uv run hyejin-bot dev fire pr-review --pr "owner/repo#42"
```

Within ~30 seconds you should see the review on the PR — Summary in the
review body, inline comments anchored to specific lines.

Inspect what the bot did:

```bash
uv run hyejin-bot inspect pr-review --pr "owner/repo#42"
```

(Outputs the `pr_review_audit` row: status, review_id, submitted_at,
inline_comment_count, persona name and mtime_ns at review time.)

---

## 4. Verify the auto-trigger

Ask a collaborator to add you as a reviewer on a PR (or use a second
account):

```bash
# Watch the structured log:
tail -f ~/.hyejin-bot/launchd.out.log | jq 'select(.event == "gh_review_requested.poll" or .event == "pr_review.posted")'
```

Within ~5 minutes (one polling cycle) the trigger emits the event and the
handler posts the review.

---

## 5. Force a re-review at the same SHA

When you want a fresh pass without a new push:

```bash
uv run hyejin-bot dev fire pr-review --pr "owner/repo#42" --force
```

The new review's Summary first line will read:

```
Updated review for SHA <sha> (supersedes earlier bot review posted at HH:MM:SS UTC)
```

The prior review remains in PR history (GitHub doesn't permit deleting
`event=COMMENT` reviews via API).

---

## 6. Pause / resume

The existing daemon kill-switch applies — when paused, no review comments
post to GitHub:

```bash
uv run hyejin-bot lifecycle pause   --reason "ooo for the day"
uv run hyejin-bot lifecycle resume
```

Pending review-requested events stay queued during the pause; they all
process after resume (no duplicates, no losses — see SC-007).

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `hyejin-bot run` exits 78 right after enabling pr_review | `gh auth status` is broken or `gh` not on PATH | `gh auth login` (or `gh auth refresh`) and restart the daemon |
| Review never posts on a small PR | persona file missing or too short → `DeadLetter` | `hyejin-bot inspect dlq` — look for `persona unavailable`; fix SKILL.md; `hyejin-bot ops replay <event_id> --confirm` |
| "PR too large for automated review" Summary on a PR you wanted reviewed | `>1000` lines or `>50` files | Either split the PR, or raise `[handlers.pr_review.size_budget]` thresholds in config and reload |
| Auto-trigger fires but review fails 422 from GitHub | inline anchor logic bug (should be impossible — anchor filter runs before posting) | Capture the failing event id and file an issue; meanwhile `--force` retries the manual path |
| Persona edits not reflected in next review | mtime didn't bump (e.g., editor wrote in place without changing mtime, or filesystem with second-resolution mtime) | `touch ~/.claude/skills/<name>/SKILL.md` to bump mtime, then trigger again |

---

## 8. What still happens automatically

Everything the existing daemon already provides:

- **At-least-once delivery** — every review request runs to completion or
  ends in `dead_letter` for operator inspection.
- **Crash recovery** — interrupted reviews are re-attempted on next boot
  (the handler is `idempotent=True`, so dedup_keys keeps repeats safe).
- **Heartbeat self-alert** — if the polling task hangs, the heartbeat
  emits `heartbeat.tick_lag` to launchd-stderr / journald.
- **Hot SQLite backup** — `pr_review_audit` history is backed up by
  `just backup` along with the rest of `state.db`.
- **Events retention** — audit rows older than 90 days (default) prune
  alongside the events they reference.
