#!/usr/bin/env bash
# Helper: store the Anthropic API key in the macOS Keychain.
#
# Usage:
#   ./scripts/setup-token.sh                # initial install: store the key
#   ./scripts/setup-token.sh --rotate       # rotate: store + restart agent,
#                                           # rolling back on restart failure
#   ./scripts/setup-token.sh -h | --help
#
# Mint a key at https://console.anthropic.com/settings/keys and paste it
# when this script prompts. Requires macOS (`security` CLI). On Linux,
# follow `docs/RUNBOOK.md` §3.2 step 3 (or use Vault via
# scripts/bootstrap-vault-approle.sh).
set -euo pipefail

usage() {
  sed -n '2,12p' "$0" | sed 's|^# \{0,1\}||'
}

ROTATE=0
SERVICE="hyejin-bot"
ACCOUNT="claude_api_key"
LABEL="ai.rebellions.hyejin-bot"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate) ROTATE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --account) ACCOUNT="$2"; shift 2 ;;
    *) echo "setup-token.sh: unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname)" != "Darwin" ]]; then
  echo "setup-token.sh: macOS only — on Linux, see docs/RUNBOOK.md §3.2." >&2
  exit 1
fi

if [[ "$ROTATE" -eq 1 ]]; then
  # Snapshot the existing key so we can roll back on restart failure.
  if PREV_TOKEN="$(security find-generic-password -s "$SERVICE" -a "$ACCOUNT" -w 2>/dev/null)"; then
    HAS_PREV=1
  else
    HAS_PREV=0
    echo "rotate: no existing key in Keychain — proceeding without rollback safety net." >&2
  fi
fi

echo "Storing Anthropic API key in Keychain (service=$SERVICE, account=$ACCOUNT)."
echo "Tip: mint a key at https://console.anthropic.com/settings/keys"
read -rsp "Anthropic API key: " TOKEN
echo

if [[ -z "$TOKEN" ]]; then
  echo "no key given; aborted." >&2
  exit 1
fi

# Replace existing entry if present.
security delete-generic-password -s "$SERVICE" -a "$ACCOUNT" 2>/dev/null || true
security add-generic-password -s "$SERVICE" -a "$ACCOUNT" -w "$TOKEN"

if [[ "$ROTATE" -ne 1 ]]; then
  echo "stored. Verify with:  security find-generic-password -s $SERVICE -a $ACCOUNT"
  exit 0
fi

# ── Rotate path ────────────────────────────────────────────────────────────
# Restart the launchd agent so the daemon picks up the new key. On
# failure roll the Keychain back to the previous key, then exit non-zero.

TARGET="gui/$(id -u)/$LABEL"
echo "rotate: restarting launchd agent ($TARGET)."

if launchctl kickstart -k "$TARGET"; then
  echo "rotate: agent restart issued. Verify with:  just doctor"
  exit 0
fi

echo "rotate: launchctl kickstart failed — rolling Keychain back." >&2
security delete-generic-password -s "$SERVICE" -a "$ACCOUNT" 2>/dev/null || true
if [[ "$HAS_PREV" -eq 1 ]]; then
  security add-generic-password -s "$SERVICE" -a "$ACCOUNT" -w "$PREV_TOKEN"
  echo "rotate: previous key restored." >&2
else
  echo "rotate: no previous key to restore; Keychain is now empty." >&2
fi
echo "rotate: investigate the agent (\`launchctl print $TARGET\`) and retry." >&2
exit 1
