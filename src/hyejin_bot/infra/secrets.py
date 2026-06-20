"""Secret provider abstraction (PLAN.md §5 Phase 4 / CONTRACTS §7).

Provider order is decided by the operator via config:
    - keychain (macOS): uses `keyring` against a (service, account) pair.
    - file:           0o600 file on disk; we refuse looser permissions.
    - env:            `ANTHROPIC_API_KEY` env var, only with the
                      `--insecure-env` flag because the key then shows
                      up in /proc/<pid>/environ on Linux.

The key is held only in memory. After construction, providers do not
expose a way to round-trip the secret out to anything except the SDK.

In addition to the daemon's Claude API key, named secrets (e.g. `jira_user`,
`jira_api_token`, `ssw_automation_password` — see feature 002) are loaded
via `load_secret(key)` on the same provider. Lookup conventions:

| Provider | Claude API key | Named secret (key=`<snake>`) |
|---|---|---|
| keychain | (service, account="claude_api_key") | (service, account=key) |
| file     | `file_path` | `file_path.parent / key` (also 0o600) |
| env      | `ANTHROPIC_API_KEY` | env var `key.upper()` (e.g. `JIRA_USER`) |
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import keyring
import keyring.errors

from hyejin_bot.core.errors import AuthError, ConfigError


@runtime_checkable
class SecretsProvider(Protocol):
    """The minimal contract a secrets backend must satisfy."""

    def load_claude_api_key(self) -> str: ...
    def load_secret(self, key: str) -> str: ...


@dataclass(frozen=True, slots=True)
class KeychainSecrets:
    """macOS Keychain (login keychain) lookup via `keyring`."""

    service: str
    account: str

    def load_claude_api_key(self) -> str:
        return self._lookup(self.account)

    def load_secret(self, key: str) -> str:
        """Look up `(self.service, key)` in keychain."""
        if not key:
            raise ConfigError("keychain: empty secret key")
        return self._lookup(key)

    def _lookup(self, account: str) -> str:
        try:
            token = keyring.get_password(self.service, account)
        except keyring.errors.KeyringError as exc:
            # No backend on Linux without secretstorage/kwallet, locked keyring,
            # or any other backend-side failure. Surface as AuthError so the
            # doctor reports `fail` cleanly instead of crashing on boot.
            raise AuthError(f"keychain: backend unavailable ({exc})") from exc
        if not token:
            raise AuthError(f"keychain: no secret for service={self.service!r} account={account!r}")
        return token


@dataclass(frozen=True, slots=True)
class FileSecrets:
    """0o600 file on disk. Refuses to load if perms are looser.

    Named secrets live as sibling files next to the Claude API key file.
    Example: `path=/etc/hyejin-bot/claude_api_key`, then
    `load_secret("jira_api_token")` reads `/etc/hyejin-bot/jira_api_token`.
    """

    path: Path

    def load_claude_api_key(self) -> str:
        return self._read_token(self.path)

    def load_secret(self, key: str) -> str:
        """Read `<self.path.parent>/<key>` as a 0o600 file."""
        if not key:
            raise ConfigError("file secrets: empty secret key")
        # Guard against path-traversal in key (slashes, dotdot).
        if "/" in key or ".." in key or key.startswith("."):
            raise ConfigError(f"file secrets: invalid key {key!r}")
        return self._read_token(self.path.parent / key)

    @staticmethod
    def _read_token(path: Path) -> str:
        if not path.exists():
            raise AuthError(f"file secrets: missing {path}")
        st = path.stat()
        if _is_world_or_group_readable(st.st_mode):
            raise ConfigError(
                f"file secrets: {path} has perms {oct(st.st_mode & 0o777)};"
                " expected 0o600 (chmod 600 the file)"
            )
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise AuthError(f"file secrets: {path} is empty")
        return token


@dataclass(frozen=True, slots=True)
class EnvSecrets:
    """Reads ANTHROPIC_API_KEY from env. Insecure on Linux (/proc visible).

    Named secrets follow the env-var convention `KEY.upper()` —
    `load_secret("jira_user")` reads `os.environ["JIRA_USER"]`.
    """

    var_name: str = "ANTHROPIC_API_KEY"

    def load_claude_api_key(self) -> str:
        token = os.environ.get(self.var_name)
        if not token:
            raise AuthError(f"env secrets: {self.var_name} not set")
        return token

    def load_secret(self, key: str) -> str:
        if not key:
            raise ConfigError("env secrets: empty secret key")
        env_name = key.upper()
        token = os.environ.get(env_name)
        if not token:
            raise AuthError(f"env secrets: {env_name} not set")
        return token


@dataclass(frozen=True, slots=True)
class VaultSecrets:
    """HashiCorp Vault provider — AppRole login + KV v2 read.

    Each lookup performs a fresh AppRole login (no token caching in this
    provider), reads the configured KV path, then revokes the token. This
    matches v1 pr-review-bot's `vault-fetch-secrets.sh` lifecycle — the
    token never outlives the call. For high-volume callers, plug a
    persistent `http_client` and rely on Vault token TTL instead.

    Field mapping:
        load_claude_api_key() → KV path field `claude_api_key_field`
                                (default `ANTHROPIC_API_KEY`).
        load_secret(key)      → KV path field `key.upper()`.
    """

    addr: str
    role_id_path: Path
    secret_id_path: Path
    kv_mount: str
    kv_path: str
    claude_api_key_field: str = "ANTHROPIC_API_KEY"
    timeout_s: float = 5.0
    http_client: httpx.Client | None = field(default=None)

    def load_claude_api_key(self) -> str:
        """Return the API key from KV, or "" if the operator opted into the
        OAuth/credentials-file path (field absent or empty in KV).

        See `infra/claude.py:RealClaudeSession` — empty key tells the CLI
        adapter to leave env clean so claude can read `~/.claude/.credentials.json`.
        """
        data = self._read_kv()
        value = data.get(self.claude_api_key_field, "")
        return value if isinstance(value, str) else ""

    def load_secret(self, key: str) -> str:
        if not key:
            raise ConfigError("vault: empty secret key")
        data = self._read_kv()
        return self._field(data, key.upper())

    def _read_kv(self) -> dict[str, Any]:
        role_id = self._read_id_file(self.role_id_path, "role_id")
        secret_id = self._read_id_file(self.secret_id_path, "secret_id")
        client = self.http_client or httpx.Client(timeout=self.timeout_s)
        owns_client = self.http_client is None
        try:
            token = self._approle_login(client, role_id, secret_id)
            try:
                return self._kv_read(client, token)
            finally:
                self._revoke(client, token)
        finally:
            if owns_client:
                client.close()

    def _approle_login(self, client: httpx.Client, role_id: str, secret_id: str) -> str:
        url = f"{self.addr.rstrip('/')}/v1/auth/approle/login"
        try:
            resp = client.post(url, json={"role_id": role_id, "secret_id": secret_id})
        except httpx.HTTPError as exc:
            raise AuthError(f"vault: approle login failed ({exc})") from exc
        if resp.status_code != 200:
            raise AuthError(
                f"vault: approle login http {resp.status_code}: {resp.text[:200]}"
            )
        token = resp.json().get("auth", {}).get("client_token")
        if not isinstance(token, str) or not token:
            raise AuthError("vault: approle login returned no client_token")
        return token

    def _kv_read(self, client: httpx.Client, token: str) -> dict[str, Any]:
        url = f"{self.addr.rstrip('/')}/v1/{self.kv_mount}/data/{self.kv_path}"
        try:
            resp = client.get(url, headers={"X-Vault-Token": token})
        except httpx.HTTPError as exc:
            raise AuthError(f"vault: kv read failed ({exc})") from exc
        if resp.status_code == 403:
            raise AuthError(f"vault: kv read forbidden at {self.kv_mount}/data/{self.kv_path}")
        if resp.status_code == 404:
            raise AuthError(f"vault: kv path not found: {self.kv_mount}/data/{self.kv_path}")
        if resp.status_code != 200:
            raise AuthError(f"vault: kv read http {resp.status_code}: {resp.text[:200]}")
        payload = resp.json().get("data", {}).get("data")
        if not isinstance(payload, dict):
            raise AuthError("vault: kv response missing data.data object")
        return payload

    def _revoke(self, client: httpx.Client, token: str) -> None:
        # Best-effort. A leaked short-TTL token is annoying but not fatal.
        url = f"{self.addr.rstrip('/')}/v1/auth/token/revoke-self"
        try:
            client.post(url, headers={"X-Vault-Token": token})
        except httpx.HTTPError:
            pass

    @staticmethod
    def _read_id_file(path: Path, label: str) -> str:
        expanded = path.expanduser()
        if not expanded.exists():
            raise AuthError(f"vault: {label} file missing: {expanded}")
        st = expanded.stat()
        if _is_world_or_group_readable(st.st_mode):
            raise ConfigError(
                f"vault: {label} file {expanded} perms {oct(st.st_mode & 0o777)};"
                " expected 0o600 (chmod 600)"
            )
        value = expanded.read_text(encoding="utf-8").strip()
        if not value:
            raise AuthError(f"vault: {label} file {expanded} is empty")
        return value

    @staticmethod
    def _field(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise AuthError(f"vault: field {key!r} missing from KV payload")
        return value


def build_provider(
    *,
    name: str,
    keychain_service: str,
    keychain_account: str,
    file_path: str,
    insecure_env_allowed: bool = False,
    vault_addr: str = "",
    vault_role_id_path: str = "",
    vault_secret_id_path: str = "",
    vault_kv_mount: str = "secret",
    vault_kv_path: str = "",
    vault_claude_api_key_field: str = "ANTHROPIC_API_KEY",
    vault_timeout_seconds: float = 5.0,
) -> SecretsProvider:
    """Construct a provider per the named strategy.

    `env` requires `insecure_env_allowed=True`; otherwise we raise
    ConfigError so the operator opts in explicitly.

    `vault` reads from HashiCorp Vault via AppRole. Requires
    `vault_role_id_path` / `vault_secret_id_path` / `vault_kv_path` set.
    """
    if name == "keychain":
        return KeychainSecrets(service=keychain_service, account=keychain_account)
    if name == "file":
        return FileSecrets(path=Path(file_path).expanduser())
    if name == "env":
        if not insecure_env_allowed:
            raise ConfigError(
                "secrets.provider='env' requires --insecure-env (api key would be visible"
                " in /proc/<pid>/environ on Linux)"
            )
        return EnvSecrets()
    if name == "vault":
        if not (vault_addr and vault_role_id_path and vault_secret_id_path and vault_kv_path):
            raise ConfigError(
                "secrets.provider='vault' requires vault_addr, vault_role_id_path,"
                " vault_secret_id_path, vault_kv_path"
            )
        return VaultSecrets(
            addr=vault_addr,
            role_id_path=Path(vault_role_id_path).expanduser(),
            secret_id_path=Path(vault_secret_id_path).expanduser(),
            kv_mount=vault_kv_mount,
            kv_path=vault_kv_path,
            claude_api_key_field=vault_claude_api_key_field,
            timeout_s=vault_timeout_seconds,
        )
    raise ConfigError(
        f"unknown secrets provider: {name!r} (expected keychain|file|env|vault)"
    )


def _is_world_or_group_readable(mode: int) -> bool:
    """True if the file is readable by group or world (any of g/o read bits set)."""
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH))


__all__ = [
    "EnvSecrets",
    "FileSecrets",
    "KeychainSecrets",
    "SecretsProvider",
    "VaultSecrets",
    "build_provider",
]
