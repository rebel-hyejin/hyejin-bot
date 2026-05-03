"""Inspect commands: status / tail / events / triggers / handlers."""

from __future__ import annotations

import typer

app = typer.Typer(help="Inspect runtime state and history.", no_args_is_help=True)


@app.command("status", help="Snapshot of outbox, in-flight, quarantined, and quota.")
def status() -> None:
    raise NotImplementedError("Phase 3: read state.db and render summary")


@app.command("tail", help="Tail the structlog stream (when running under journalctl/launchctl).")
def tail() -> None:
    raise NotImplementedError("Phase 3: platform-aware log tail (journalctl / log stream)")


events = typer.Typer(help="List or inspect events.", no_args_is_help=True)
triggers = typer.Typer(help="List triggers and unquarantine.", no_args_is_help=True)
handlers = typer.Typer(help="List handlers.", no_args_is_help=True)
app.add_typer(events, name="events")
app.add_typer(triggers, name="triggers")
app.add_typer(handlers, name="handlers")


@events.command("ls", help="List recent events.")
def events_ls() -> None:
    raise NotImplementedError("Phase 3")


@events.command("get", help="Show a single event with its outbox/runs history.")
def events_get(event_id: str) -> None:
    raise NotImplementedError("Phase 3")


@triggers.command("ls", help="List configured triggers and quarantine status.")
def triggers_ls() -> None:
    raise NotImplementedError("Phase 3")


@triggers.command("unquarantine", help="Clear quarantine flag for a trigger.")
def triggers_unquarantine(name: str) -> None:
    raise NotImplementedError("Phase 3")


@handlers.command("ls", help="List configured handlers and their manifests.")
def handlers_ls() -> None:
    raise NotImplementedError("Phase 3")
