"""VaultSecrets provider — AppRole login + KV v2 read + revoke."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from hyejin_bot.core.errors import AuthError, ConfigError
from hyejin_bot.infra.secrets import VaultSecrets, build_provider


def _write_0600(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _kv_payload(fields: dict[str, str]) -> dict[str, object]:
    return {"data": {"data": fields, "metadata": {"version": 1}}}


def _build_provider(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    claude_api_key_field: str = "ANTHROPIC_API_KEY",
) -> VaultSecrets:
    role_id = tmp_path / "role_id"
    secret_id = tmp_path / "secret_id"
    _write_0600(role_id, "role-abc\n")
    _write_0600(secret_id, "secret-xyz\n")
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, timeout=5.0)
    return VaultSecrets(
        addr="https://vault.example",
        role_id_path=role_id,
        secret_id_path=secret_id,
        kv_mount="secret",
        kv_path="bots/hyejin-bot",
        claude_api_key_field=claude_api_key_field,
        http_client=client,
    )


def test_load_claude_api_key_happy_path(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.url.path == "/v1/auth/approle/login":
            body = json.loads(req.content.decode())
            assert body == {"role_id": "role-abc", "secret_id": "secret-xyz"}
            return httpx.Response(200, json={"auth": {"client_token": "vault-tok"}})
        if req.url.path == "/v1/secret/data/bots/hyejin-bot":
            assert req.headers.get("x-vault-token") == "vault-tok"
            return httpx.Response(200, json=_kv_payload({"ANTHROPIC_API_KEY": "sk-ant-vault"}))
        if req.url.path == "/v1/auth/token/revoke-self":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {req.method} {req.url}")

    provider = _build_provider(tmp_path, _handler)
    assert provider.load_claude_api_key() == "sk-ant-vault"
    assert ("POST", "/v1/auth/approle/login") in calls
    assert ("GET", "/v1/secret/data/bots/hyejin-bot") in calls
    assert ("POST", "/v1/auth/token/revoke-self") in calls


def test_load_secret_uppercases_key(tmp_path: Path) -> None:
    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/approle/login":
            return httpx.Response(200, json={"auth": {"client_token": "tok"}})
        if req.url.path == "/v1/secret/data/bots/hyejin-bot":
            return httpx.Response(
                200,
                json=_kv_payload({"JIRA_USER": "automation@rebellions.ai"}),
            )
        return httpx.Response(204)

    provider = _build_provider(tmp_path, _handler)
    assert provider.load_secret("jira_user") == "automation@rebellions.ai"


def test_missing_field_raises_auth_error(tmp_path: Path) -> None:
    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/approle/login":
            return httpx.Response(200, json={"auth": {"client_token": "tok"}})
        if req.url.path == "/v1/secret/data/bots/hyejin-bot":
            return httpx.Response(200, json=_kv_payload({"OTHER": "x"}))
        return httpx.Response(204)

    provider = _build_provider(tmp_path, _handler)
    with pytest.raises(AuthError, match="ANTHROPIC_API_KEY"):
        provider.load_claude_api_key()


def test_approle_login_failure_maps_to_auth_error(tmp_path: Path) -> None:
    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/approle/login":
            return httpx.Response(400, text="invalid role")
        return httpx.Response(500)

    provider = _build_provider(tmp_path, _handler)
    with pytest.raises(AuthError, match="approle login http 400"):
        provider.load_claude_api_key()


def test_kv_read_403_forbidden(tmp_path: Path) -> None:
    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/approle/login":
            return httpx.Response(200, json={"auth": {"client_token": "tok"}})
        if req.url.path == "/v1/secret/data/bots/hyejin-bot":
            return httpx.Response(403, text="permission denied")
        return httpx.Response(204)

    provider = _build_provider(tmp_path, _handler)
    with pytest.raises(AuthError, match="forbidden"):
        provider.load_claude_api_key()


def test_kv_read_404_not_found(tmp_path: Path) -> None:
    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/approle/login":
            return httpx.Response(200, json={"auth": {"client_token": "tok"}})
        if req.url.path == "/v1/secret/data/bots/hyejin-bot":
            return httpx.Response(404, text="not found")
        return httpx.Response(204)

    provider = _build_provider(tmp_path, _handler)
    with pytest.raises(AuthError, match="not found"):
        provider.load_claude_api_key()


def test_id_file_missing_raises(tmp_path: Path) -> None:
    def _noop(_req: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP should not be called when id files are missing")

    transport = httpx.MockTransport(_noop)
    client = httpx.Client(transport=transport)
    provider = VaultSecrets(
        addr="https://vault.example",
        role_id_path=tmp_path / "absent.role_id",
        secret_id_path=tmp_path / "absent.secret_id",
        kv_mount="secret",
        kv_path="bots/hyejin-bot",
        http_client=client,
    )
    with pytest.raises(AuthError, match="role_id file missing"):
        provider.load_claude_api_key()


def test_id_file_loose_perms_rejected(tmp_path: Path) -> None:
    role_id = tmp_path / "role_id"
    secret_id = tmp_path / "secret_id"
    role_id.write_text("role-abc\n", encoding="utf-8")
    role_id.chmod(0o640)
    _write_0600(secret_id, "secret-xyz\n")

    transport = httpx.MockTransport(lambda _r: httpx.Response(200))
    client = httpx.Client(transport=transport)
    provider = VaultSecrets(
        addr="https://vault.example",
        role_id_path=role_id,
        secret_id_path=secret_id,
        kv_mount="secret",
        kv_path="bots/hyejin-bot",
        http_client=client,
    )
    with pytest.raises(ConfigError, match="0o600"):
        provider.load_claude_api_key()


def test_build_provider_vault_requires_paths() -> None:
    with pytest.raises(ConfigError, match="requires vault_addr"):
        build_provider(
            name="vault",
            keychain_service="",
            keychain_account="",
            file_path="",
        )


def test_build_provider_vault_constructs() -> None:
    provider = build_provider(
        name="vault",
        keychain_service="",
        keychain_account="",
        file_path="",
        vault_addr="https://vault.example",
        vault_role_id_path="~/role_id",
        vault_secret_id_path="~/secret_id",
        vault_kv_path="bots/hyejin-bot",
    )
    assert isinstance(provider, VaultSecrets)
    assert provider.addr == "https://vault.example"


def test_load_secret_empty_key_rejected(tmp_path: Path) -> None:
    def _handler(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call HTTP for empty key")

    provider = _build_provider(tmp_path, _handler)
    with pytest.raises(ConfigError, match="empty secret key"):
        provider.load_secret("")
