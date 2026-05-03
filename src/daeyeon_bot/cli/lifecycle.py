"""Lifecycle commands: run / pause / resume / stop."""

from __future__ import annotations

import typer

app = typer.Typer(help="Lifecycle controls: pause, resume, stop.", no_args_is_help=True)


def run() -> None:
    """Start the daemon. Phase 0 stub — returns NotImplementedError to make doctor honest."""
    raise NotImplementedError("Phase 1: implement lifecycle.boot() + signal-driven shutdown loop.")


@app.command("pause", help="Create the PAUSE flag; running handlers continue, new ones block.")
def pause() -> None:
    raise NotImplementedError("Phase 3: touch PAUSE file in state_dir")


@app.command("resume", help="Remove the PAUSE flag; new handlers may proceed.")
def resume() -> None:
    raise NotImplementedError("Phase 3: unlink PAUSE file in state_dir")


@app.command("stop", help="Send SIGTERM to the running daemon (pidfile lookup).")
def stop() -> None:
    raise NotImplementedError("Phase 2: read pidfile + os.kill(pid, SIGTERM)")
