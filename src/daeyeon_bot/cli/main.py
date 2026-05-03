"""Top-level Typer app.

Subcommand groups:
    daeyeon-bot run                 (lifecycle, hot path)
    daeyeon-bot lifecycle pause|resume|stop
    daeyeon-bot inspect status|tail|events|triggers|handlers
    daeyeon-bot ops doctor|migrate|replay|prune
    daeyeon-bot dev fire|call|repl
"""

from __future__ import annotations

import typer

from daeyeon_bot import __version__
from daeyeon_bot.cli.dev import app as dev_app
from daeyeon_bot.cli.inspect import app as inspect_app
from daeyeon_bot.cli.lifecycle import app as lifecycle_app
from daeyeon_bot.cli.lifecycle import run as run_command
from daeyeon_bot.cli.ops import app as ops_app

app = typer.Typer(
    name="daeyeon-bot",
    help="Personal Claude bot daemon. Run `daeyeon-bot run` to start; everything else inspects or operates.",
    add_completion=False,
)

app.command("run", help="Start the daemon (foreground). Use launchd / systemd in production.")(
    run_command
)

app.add_typer(lifecycle_app, name="lifecycle")
app.add_typer(inspect_app, name="inspect")
app.add_typer(ops_app, name="ops")
app.add_typer(dev_app, name="dev")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Print version and exit.",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":
    app()
