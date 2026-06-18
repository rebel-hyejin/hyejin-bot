"""Top-level Typer app.

Subcommand groups:
    hyejin-bot run                 (lifecycle, hot path)
    hyejin-bot lifecycle pause|resume|stop
    hyejin-bot inspect status|tail|events|triggers|handlers
    hyejin-bot ops doctor|migrate|replay|prune
    hyejin-bot dev fire|call|repl
"""

from __future__ import annotations

import typer

from hyejin_bot import __version__
from hyejin_bot.cli.dev import app as dev_app
from hyejin_bot.cli.inspect import app as inspect_app
from hyejin_bot.cli.lifecycle import app as lifecycle_app
from hyejin_bot.cli.lifecycle import run as run_command
from hyejin_bot.cli.ops import app as ops_app

app = typer.Typer(
    name="hyejin-bot",
    help="Personal Claude bot daemon. Run `hyejin-bot run` to start; everything else inspects or operates.",
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
def root(
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
