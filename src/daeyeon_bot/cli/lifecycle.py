"""Lifecycle commands: run / pause / resume / stop."""

from __future__ import annotations

import asyncio
import os
import signal

import typer

from daeyeon_bot.app import pause as pause_mod
from daeyeon_bot.app.config import load
from daeyeon_bot.app.lifecycle import AlreadyRunningError, BootOptions, boot
from daeyeon_bot.core.errors import AuthError, ConfigError

app = typer.Typer(help="Lifecycle controls: pause, resume, stop.", no_args_is_help=True)


def run(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
    insecure_env: bool = typer.Option(
        False,
        "--insecure-env",
        help="Allow secrets.provider='env' (token visible in /proc on Linux).",
    ),
) -> None:
    """Start the daemon (foreground). Use launchd / systemd in production."""
    try:
        asyncio.run(boot(BootOptions(config_path=config, insecure_env_allowed=insecure_env)))
    except AlreadyRunningError as exc:
        typer.echo(f"daeyeon-bot: {exc}", err=True)
        # 75 = EX_TEMPFAIL — supervisor (launchd/systemd) should retry later.
        raise typer.Exit(code=75) from exc
    except (AuthError, ConfigError) as exc:
        typer.echo(f"daeyeon-bot: {type(exc).__name__}: {exc}", err=True)
        # 78 = EX_CONFIG — operator must rotate the token / fix config.
        raise typer.Exit(code=78) from exc


@app.command("pause", help="Create the PAUSE flag; running handlers continue, new ones block.")
def pause(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cfg = load(config)
    cfg.state_dir_path.mkdir(parents=True, exist_ok=True)
    created = pause_mod.pause(cfg.pause_flag_path)
    if created:
        typer.echo(f"paused: {cfg.pause_flag_path}")
    else:
        typer.echo(f"already paused: {cfg.pause_flag_path}")


@app.command("resume", help="Remove the PAUSE flag; new handlers may proceed.")
def resume(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cfg = load(config)
    removed = pause_mod.resume(cfg.pause_flag_path)
    if removed:
        typer.echo(f"resumed: removed {cfg.pause_flag_path}")
    else:
        typer.echo(f"not paused: {cfg.pause_flag_path} did not exist")


@app.command("stop", help="Send SIGTERM to the running daemon (pidfile lookup).")
def stop(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    _send_pidfile_signal(config, signal.SIGTERM, action="SIGTERM")


@app.command(
    "reload-config",
    help=(
        "Restart the daemon so config.toml is re-read. Persona file edits "
        "(mtime change) take effect on the next event without this — only "
        "use this when [handlers.pr_review].persona_skill itself changes."
    ),
)
def reload_config(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    _send_pidfile_signal(config, signal.SIGTERM, action="reload (SIGTERM)")
    typer.echo(
        "supervisor (launchd/systemd KeepAlive) will start a fresh daemon with the new config",
        err=True,
    )


def _send_pidfile_signal(config: str | None, sig: signal.Signals, *, action: str) -> None:
    cfg = load(config)
    pidfile = cfg.pidfile_path
    if not pidfile.exists():
        typer.echo(f"no pidfile at {pidfile}", err=True)
        raise typer.Exit(code=1)
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        typer.echo(f"unreadable pidfile {pidfile}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        typer.echo(f"no process with pid {pid} (stale pidfile?)", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{action} sent to pid {pid}")
