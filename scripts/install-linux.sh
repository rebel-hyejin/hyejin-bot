#!/usr/bin/env bash
# Register hyejin-bot as a systemd user unit (no root required).
#
# Usage:  ./scripts/install-linux.sh
#
# The Anthropic API key (and any named secrets like JIRA_USER, JIRA_API_TOKEN,
# SSW_AUTOMATION_PASSWORD) is fetched at daemon startup from HashiCorp Vault
# via secrets.provider='vault' in config.toml. Before running this script:
#
#   1. AppRole role_id and secret_id must already live as 0600 files at:
#        ~/bots/.vault/hyejin-bot.role_id
#        ~/bots/.vault/hyejin-bot.secret_id
#      (see docs or scripts/bootstrap-vault-approle.sh)
#
#   2. Vault KV v2 path `secret/bots/hyejin-bot` must already hold
#      ANTHROPIC_API_KEY=sk-ant-...
#
#   3. config.toml must exist in the repo root (cp config.example.toml,
#      tune github.username + allowed_repos).
#
# This script does NOT touch any of those — it only writes the unit file
# and starts the service.
set -euo pipefail

if [[ "$(uname)" != "Linux" ]]; then
  echo "install-linux.sh: requires Linux (got $(uname))" >&2
  exit 1
fi

PREFIX="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${HYEJIN_BOT_STATE_DIR:-$HOME/.hyejin-bot}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_NAME="hyejin-bot.service"
TARGET="$UNIT_DIR/$UNIT_NAME"
TEMPLATE="$PREFIX/deploy/systemd/$UNIT_NAME"

# Refuse to install if the operator skipped Vault bootstrap — the daemon
# would just AuthError on boot and the unit would loop on exit 78.
ROLE_ID_FILE="$HOME/bots/.vault/hyejin-bot.role_id"
SECRET_ID_FILE="$HOME/bots/.vault/hyejin-bot.secret_id"
for f in "$ROLE_ID_FILE" "$SECRET_ID_FILE"; do
  if [[ ! -f "$f" ]]; then
    echo "missing Vault AppRole file: $f" >&2
    echo "see scripts/bootstrap-vault-approle.sh (or your Vault admin)" >&2
    exit 66  # EX_NOINPUT
  fi
  mode="$(stat -c '%a' "$f")"
  if [[ "$mode" != "600" ]]; then
    echo "$f has perms $mode; expected 600 (chmod 600 $f)" >&2
    exit 78  # EX_CONFIG
  fi
done

if [[ ! -f "$PREFIX/config.toml" ]]; then
  echo "missing $PREFIX/config.toml (cp config.example.toml config.toml)" >&2
  exit 66
fi

mkdir -p "$STATE_DIR" "$UNIT_DIR"
chmod 0700 "$STATE_DIR"

sed \
  -e "s|__INSTALL_PREFIX__|$PREFIX|g" \
  -e "s|__STATE_DIR__|$STATE_DIR|g" \
  "$TEMPLATE" > "$TARGET"

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

echo "installed: $TARGET"
echo "status:    systemctl --user status $UNIT_NAME"
echo "logs:      journalctl --user -u $UNIT_NAME -f"
echo "stop:      systemctl --user stop $UNIT_NAME"
