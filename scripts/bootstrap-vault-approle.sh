#!/usr/bin/env bash
# Bootstrap Vault AppRole credentials for hyejin-bot.
#
# Usage:  ./scripts/bootstrap-vault-approle.sh
#
# Prerequisites (Vault admin-side, done once out of band):
#   * KV v2 mount `secret/` exists.
#   * Path `secret/bots/hyejin-bot` holds the daemon's runtime secrets, e.g.:
#       vault kv put secret/bots/hyejin-bot \
#         ANTHROPIC_API_KEY=sk-ant-api03-... \
#         JIRA_USER=automation@rebellions.ai \
#         JIRA_API_TOKEN=... \
#         SSW_AUTOMATION_PASSWORD=...
#   * Read policy `hyejin-bot-ro` covers that path:
#       path "secret/data/bots/hyejin-bot"     { capabilities = ["read"] }
#       path "secret/metadata/bots/hyejin-bot" { capabilities = ["read"] }
#       path "auth/token/revoke-self"          { capabilities = ["update"] }
#   * AppRole role `hyejin-bot` exists bound to that policy:
#       vault write auth/approle/role/hyejin-bot \
#         token_policies=hyejin-bot-ro token_ttl=15m token_max_ttl=30m
#
# This script (operator-side, runnable as `hyejin.han`):
#   1. Logs into Vault interactively (must have `vault login` already done).
#   2. Reads the role_id (constant per role) and a fresh secret_id.
#   3. Writes both as 0600 files under ~/bots/.vault/.
#
# Re-run any time the secret_id expires.
set -euo pipefail

ROLE="${1:-hyejin-bot}"
VAULT_ADDR="${VAULT_ADDR:-https://vault.ssw.rbln.in}"
export VAULT_ADDR

OUT_DIR="$HOME/bots/.vault"
mkdir -p "$OUT_DIR"
chmod 0700 "$OUT_DIR"

ROLE_ID_FILE="$OUT_DIR/$ROLE.role_id"
SECRET_ID_FILE="$OUT_DIR/$ROLE.secret_id"

if ! command -v vault >/dev/null 2>&1; then
  echo "vault CLI not found; install from https://developer.hashicorp.com/vault/install" >&2
  exit 127
fi

if ! vault token lookup >/dev/null 2>&1; then
  echo "vault: not logged in. Run \`vault login -method=oidc\` (or your usual auth) first." >&2
  exit 78  # EX_CONFIG
fi

umask 077
vault read -field=role_id "auth/approle/role/$ROLE/role-id" > "$ROLE_ID_FILE"
vault write -force -field=secret_id "auth/approle/role/$ROLE/secret-id" > "$SECRET_ID_FILE"

chmod 0600 "$ROLE_ID_FILE" "$SECRET_ID_FILE"

echo "wrote $ROLE_ID_FILE (mode 600)"
echo "wrote $SECRET_ID_FILE (mode 600)"
echo
echo "Next: scripts/install-linux.sh"
