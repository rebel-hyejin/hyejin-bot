"""Dev commands: fire / call / repl. Not for production use."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Developer utilities: fire triggers, call handlers, IPython repl.", no_args_is_help=True
)


@app.command(
    "fire",
    help="Fire a trigger to emit an event (writes through the normal outbox path).",
)
def fire(trigger: str, message: str = typer.Option("", "--message", "-m")) -> None:
    raise NotImplementedError("Phase 1: enqueue Event for the named trigger")


@app.command(
    "call",
    help="Invoke a handler directly with an event JSON, BYPASSING the outbox. Dev only.",
)
def call(handler: str, event_json: str = typer.Option(..., "--event-json")) -> None:
    raise NotImplementedError("Phase 3: bypass outbox, call handler.handle() with parsed event")


@app.command("repl", help="Drop into IPython with the production container bound.")
def repl() -> None:
    raise NotImplementedError("Phase 3: IPython.embed with container in scope")
