"""Phase 0 CLI sanity: --help works without booting the daemon."""

from __future__ import annotations

from typer.testing import CliRunner

from hyejin_bot.cli.main import app


def test_root_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "hyejin-bot" in result.stdout.lower() or "daemon" in result.stdout.lower()


def test_subcommand_groups_present() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    for group in ("run", "lifecycle", "inspect", "ops", "dev"):
        assert group in out, f"missing subcommand group: {group}\n{out}"


def test_version_flag() -> None:
    from hyejin_bot import __version__

    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
