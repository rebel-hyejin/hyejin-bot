"""Secret provider abstraction (PLAN.md §5 Phase 4 / CONTRACTS §7).

Provider order is decided by the operator via config:
    - keychain (macOS): uses `keyring` against a (service, account) pair.
    - file:           0o600 file on disk; we refuse looser permissions.
    - env:            `CLAUDE_CODE_OAUTH_TOKEN` env var, only with the
                      `--insecure-env` flag because the token then shows
                      up in /proc/<pid>/environ on Linux.

The token is held only in memory. After construction, providers do not
expose a way to round-trip the secret out to anything except the SDK.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import keyring

from daeyeon_bot.core.errors import AuthError, ConfigError


@runtime_checkable
class SecretsProvider(Protocol):
    """The minimal contract a secrets backend must satisfy."""

    def load_oauth_token(self) -> str: ...


@dataclass(frozen=True, slots=True)
class KeychainSecrets:
    """macOS Keychain (login keychain) lookup via `keyring`."""

    service: str
    account: str

    def load_oauth_token(self) -> str:
        token = keyring.get_password(self.service, self.account)
        if not token:
            raise AuthError(
                f"keychain: no token for service={self.service!r} account={self.account!r}"
            )
        return token


@dataclass(frozen=True, slots=True)
class FileSecrets:
    """0o600 file on disk. Refuses to load if perms are looser."""

    path: Path

    def load_oauth_token(self) -> str:
        if not self.path.exists():
            raise AuthError(f"file secrets: missing {self.path}")
        st = self.path.stat()
        if _is_world_or_group_readable(st.st_mode):
            raise ConfigError(
                f"file secrets: {self.path} has perms {oct(st.st_mode & 0o777)};"
                " expected 0o600 (chmod 600 the file)"
            )
        token = self.path.read_text(encoding="utf-8").strip()
        if not token:
            raise AuthError(f"file secrets: {self.path} is empty")
        return token


@dataclass(frozen=True, slots=True)
class EnvSecrets:
    """Reads CLAUDE_CODE_OAUTH_TOKEN from env. Insecure on Linux (/proc visible)."""

    var_name: str = "CLAUDE_CODE_OAUTH_TOKEN"

    def load_oauth_token(self) -> str:
        token = os.environ.get(self.var_name)
        if not token:
            raise AuthError(f"env secrets: {self.var_name} not set")
        return token


def build_provider(
    *,
    name: str,
    keychain_service: str,
    keychain_account: str,
    file_path: str,
    insecure_env_allowed: bool = False,
) -> SecretsProvider:
    """Construct a provider per the named strategy.

    `env` requires `insecure_env_allowed=True`; otherwise we raise
    ConfigError so the operator opts in explicitly.
    """
    if name == "keychain":
        return KeychainSecrets(service=keychain_service, account=keychain_account)
    if name == "file":
        return FileSecrets(path=Path(file_path).expanduser())
    if name == "env":
        if not insecure_env_allowed:
            raise ConfigError(
                "secrets.provider='env' requires --insecure-env (token would be visible"
                " in /proc/<pid>/environ on Linux)"
            )
        return EnvSecrets()
    raise ConfigError(f"unknown secrets provider: {name!r} (expected keychain|file|env)")


def _is_world_or_group_readable(mode: int) -> bool:
    """True if the file is readable by group or world (any of g/o read bits set)."""
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH))


__all__ = [
    "EnvSecrets",
    "FileSecrets",
    "KeychainSecrets",
    "SecretsProvider",
    "build_provider",
]
