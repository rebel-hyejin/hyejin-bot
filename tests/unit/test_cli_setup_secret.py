"""`daeyeon-bot lifecycle setup-secret <name>` unit tests."""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from daeyeon_bot.cli import lifecycle as _lifecycle
from daeyeon_bot.cli.lifecycle import app

# Private helper — accessed via module attribute for test only.
_is_valid_secret_name = _lifecycle._is_valid_secret_name  # pyright: ignore[reportPrivateUsage]

runner = CliRunner()


def _write_config(tmp_path: Path, *, provider: str, file_path: Path | None = None) -> Path:
    """Build a minimal config.toml that sets `[secrets].provider`."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    cfg_path = tmp_path / "config.toml"
    file_kw = f'file_path = "{file_path}"\n' if file_path is not None else ""
    cfg_path.write_text(
        f"""
[runtime]
state_dir = {str(state_dir)!r}

[secrets]
provider = "{provider}"
keychain_service = "daeyeon-bot-test"
{file_kw}""".lstrip(),
        encoding="utf-8",
    )
    return cfg_path


# ── Name validation ──────────────────────────────────────────────────────────


def test_valid_secret_names() -> None:
    for ok in ("jira_user", "jira_api_token", "ssw_automation_password", "x1", "_"):
        assert _is_valid_secret_name(ok), f"expected valid: {ok!r}"


def test_invalid_secret_names() -> None:
    for bad in ("", "jira-user", "Jira_User", "..", "../escape", "sub/dir", ".hidden", "JIRA_USER"):
        assert not _is_valid_secret_name(bad), f"expected invalid: {bad!r}"


def test_invalid_name_argv_exits_with_code_2(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, provider="keychain")
    result = runner.invoke(
        app,
        ["setup-secret", "BAD-NAME", "--value", "x", "--config", str(cfg_path)],
    )
    assert result.exit_code == 2
    assert "invalid name" in result.output.lower()


# ── Keychain backend ─────────────────────────────────────────────────────────


def test_keychain_provider_calls_set_password(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, provider="keychain")
    with patch("daeyeon_bot.cli.lifecycle.keyring.set_password") as set_pw:
        result = runner.invoke(
            app,
            [
                "setup-secret",
                "jira_api_token",
                "--value",
                "atok-abc",
                "--config",
                str(cfg_path),
            ],
        )
    assert result.exit_code == 0, result.output
    set_pw.assert_called_once_with("daeyeon-bot-test", "jira_api_token", "atok-abc")
    assert "stashed: keychain" in result.output


def test_keychain_backend_error_surfaced(tmp_path: Path) -> None:
    import keyring.errors

    cfg_path = _write_config(tmp_path, provider="keychain")
    with patch(
        "daeyeon_bot.cli.lifecycle.keyring.set_password",
        side_effect=keyring.errors.KeyringError("no backend"),
    ):
        result = runner.invoke(
            app,
            ["setup-secret", "jira_user", "--value", "x", "--config", str(cfg_path)],
        )
    assert result.exit_code == 1
    assert "keychain backend error" in result.output


# ── File backend ─────────────────────────────────────────────────────────────


def test_file_provider_creates_0600_file(tmp_path: Path) -> None:
    file_path = tmp_path / "secrets" / "oauth_token"
    cfg_path = _write_config(tmp_path, provider="file", file_path=file_path)

    result = runner.invoke(
        app,
        [
            "setup-secret",
            "jira_user",
            "--value",
            "daeyeon.lee@rebellions.ai",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output

    written = tmp_path / "secrets" / "jira_user"
    assert written.exists()
    assert written.read_text(encoding="utf-8").rstrip("\n") == "daeyeon.lee@rebellions.ai"
    mode = stat.S_IMODE(written.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    assert "stashed:" in result.output


def test_file_provider_overwrites_existing_secret(tmp_path: Path) -> None:
    """Re-running setup-secret on the same key replaces the value cleanly."""
    file_path = tmp_path / "secrets" / "oauth_token"
    cfg_path = _write_config(tmp_path, provider="file", file_path=file_path)

    runner.invoke(
        app,
        ["setup-secret", "jira_user", "--value", "first@x", "--config", str(cfg_path)],
    )
    result = runner.invoke(
        app,
        ["setup-secret", "jira_user", "--value", "second@x", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0
    written = tmp_path / "secrets" / "jira_user"
    assert written.read_text(encoding="utf-8").rstrip("\n") == "second@x"


def test_file_provider_enforces_0600_even_if_existed_loose(tmp_path: Path) -> None:
    """Pre-existing 0o644 sibling gets tightened to 0o600 after stash."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    loose = secrets_dir / "jira_user"
    loose.write_text("old\n", encoding="utf-8")
    loose.chmod(0o644)
    file_path = secrets_dir / "oauth_token"
    cfg_path = _write_config(tmp_path, provider="file", file_path=file_path)

    result = runner.invoke(
        app,
        ["setup-secret", "jira_user", "--value", "new@x", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0
    mode = stat.S_IMODE(loose.stat().st_mode)
    assert mode == 0o600


# ── env backend refusal ─────────────────────────────────────────────────────


def test_env_provider_refuses_to_stash(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, provider="env")
    result = runner.invoke(
        app,
        ["setup-secret", "jira_user", "--value", "x", "--config", str(cfg_path)],
    )
    assert result.exit_code == 2
    assert "Expected name: JIRA_USER" in result.output


# ── Empty value refusal ─────────────────────────────────────────────────────


def test_empty_value_rejected(tmp_path: Path) -> None:
    """When neither --value nor prompt provides a value, Click aborts (exit 1)."""
    cfg_path = _write_config(tmp_path, provider="keychain")
    result = runner.invoke(
        app,
        ["setup-secret", "jira_user", "--value", "", "--config", str(cfg_path)],
        input="\n",  # empty prompt → click re-prompts → EOF → abort
    )
    # Click's prompt aborts on EOF with exit 1. Either way, no stash happened.
    assert result.exit_code != 0


# ── Interactive prompt flow ─────────────────────────────────────────────────


def test_interactive_prompt_reads_hidden_value(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, provider="keychain")
    with patch("daeyeon_bot.cli.lifecycle.keyring.set_password") as set_pw:
        result = runner.invoke(
            app,
            ["setup-secret", "jira_api_token", "--config", str(cfg_path)],
            input="atok-from-prompt\n",
        )
    assert result.exit_code == 0, result.output
    set_pw.assert_called_once_with("daeyeon-bot-test", "jira_api_token", "atok-from-prompt")


# ── Round-trip with FileSecrets.load_secret ─────────────────────────────────


def test_stashed_file_round_trips_through_FileSecrets(tmp_path: Path) -> None:
    """Stash via CLI, then read back via the same provider the daemon uses."""
    from daeyeon_bot.infra.secrets import FileSecrets

    file_path = tmp_path / "secrets" / "oauth_token"
    # OAuth file itself doesn't need to exist for load_secret(name).
    cfg_path = _write_config(tmp_path, provider="file", file_path=file_path)
    result = runner.invoke(
        app,
        ["setup-secret", "jira_api_token", "--value", "atok-rt", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0, result.output

    provider = FileSecrets(path=file_path)
    assert provider.load_secret("jira_api_token") == "atok-rt"
