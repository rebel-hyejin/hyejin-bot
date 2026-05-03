"""Secret provider abstraction. Phase 0: stub."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsProvider(Protocol):
    def load_oauth_token(self) -> str: ...


def build_provider(name: str) -> SecretsProvider:
    raise NotImplementedError("Phase 4: keychain | file | env (--insecure-env only)")
