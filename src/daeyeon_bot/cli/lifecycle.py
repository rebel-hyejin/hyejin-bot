"""Lifecycle commands: run / pause / resume / stop."""

from __future__ import annotations

import asyncio

import typer

from daeyeon_bot.app.lifecycle import BootOptions, boot

app = typer.Typer(help="Lifecycle controls: pause, resume, stop.", no_args_is_help=True)


def run(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    """Start the daemon (foreground). Use launchd / systemd in production."""
    asyncio.run(boot(BootOptions(config_path=config)))


@app.command("pause", help="Create the PAUSE flag; running handlers continue, new ones block.")
def pause() -> None:
    raise NotImplementedError("Phase 3: touch PAUSE file in state_dir")


@app.command("resume", help="Remove the PAUSE flag; new handlers may proceed.")
def resume() -> None:
    raise NotImplementedError("Phase 3: unlink PAUSE file in state_dir")


@app.command("stop", help="Send SIGTERM to the running daemon (pidfile lookup).")
def stop() -> None:
    raise NotImplementedError("Phase 2: read pidfile + os.kill(pid, SIGTERM)")
