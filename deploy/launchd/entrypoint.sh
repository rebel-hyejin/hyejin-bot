#!/usr/bin/env bash
# launchd entrypoint. Sets a tight umask so any file the daemon creates is
# private, then exec's `hyejin-bot run` so launchd watches the right pid.
# The Anthropic API key is loaded from the macOS Keychain (or Vault) inside
# the process — never via this script's environment.
set -euo pipefail

umask 0077

cd "$(dirname "$0")/../.."

# Use `uv run` so the project venv stays the source of truth.
exec uv run hyejin-bot run "$@"
