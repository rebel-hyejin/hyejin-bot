"""Inspect commands: status / tail / events / triggers / handlers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import typer

from daeyeon_bot.app.config import Config, load
from daeyeon_bot.app.registry import build_handler_registry
from daeyeon_bot.app.supervisor import list_quarantined, unquarantine
from daeyeon_bot.infra import queries, storage

app = typer.Typer(help="Inspect runtime state and history.", no_args_is_help=True)

events = typer.Typer(help="List or inspect events.", no_args_is_help=True)
triggers = typer.Typer(help="List triggers and unquarantine.", no_args_is_help=True)
handlers = typer.Typer(help="List handlers.", no_args_is_help=True)
app.add_typer(events, name="events")
app.add_typer(triggers, name="triggers")
app.add_typer(handlers, name="handlers")


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    outbox_counts: dict[str, int]
    quarantined: list[dict[str, str]]
    db_path: Path


@app.command("status", help="Snapshot of outbox, in-flight, quarantined, and quota.")
def status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    snapshot = asyncio.run(_status(config_path=config))
    _render_status(snapshot)


async def _status(*, config_path: str | None) -> StatusSnapshot:
    cfg = load(config_path)
    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        counts = await queries.outbox_status_counts(conn)
        quarantined = await list_quarantined(conn)
    return StatusSnapshot(outbox_counts=counts, quarantined=quarantined, db_path=cfg.db_path)


def _render_status(snapshot: StatusSnapshot) -> None:
    typer.echo(f"db: {snapshot.db_path}")
    typer.echo("outbox:")
    for status_name, count in snapshot.outbox_counts.items():
        typer.echo(f"  {status_name:12s} {count}")
    typer.echo("quarantined triggers:")
    if not snapshot.quarantined:
        typer.echo("  (none)")
    for row in snapshot.quarantined:
        typer.echo(f"  {row['trigger_name']}  at={row['quarantined_at']}  reason={row['reason']}")


@app.command("tail", help="Tail the latest runs from the runs table (recent activity).")
def tail(
    n: int = typer.Option(20, "--n", help="Rows to show."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    rows = asyncio.run(_tail(limit=n, config_path=config))
    if not rows:
        typer.echo("(no runs yet)")
        return
    for run in rows:
        typer.echo(
            f"{run.finished_at or run.started_at}  {run.handler:14s}  {run.status:18s}"
            f"  ev={run.event_id}  dur={run.duration_ms}ms  by={run.triggered_by}"
        )


async def _tail(*, limit: int, config_path: str | None) -> list[queries.RunRow]:
    cfg = load(config_path)
    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        return await queries.list_runs(conn, limit=limit)


@events.command("ls", help="List recent events.")
def events_ls(
    n: int = typer.Option(20, "--n", help="Rows to show."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    rows = asyncio.run(_events_ls(limit=n, config_path=config))
    if not rows:
        typer.echo("(no events)")
        return
    for ev in rows:
        typer.echo(
            f"{ev.created_at.isoformat()}  {ev.type:24s}  src={ev.source}"
            f"  id={ev.id}  trace={ev.trace_id}"
        )


async def _events_ls(*, limit: int, config_path: str | None) -> list[queries.EventRecord]:
    cfg = load(config_path)
    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        return await queries.list_events(conn, limit=limit)


@events.command("get", help="Show a single event with its outbox/runs history.")
def events_get(
    event_id: str,
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    bundle = asyncio.run(_events_get(event_id=event_id, config_path=config))
    if bundle is None:
        typer.echo(f"event not found: {event_id}", err=True)
        raise typer.Exit(code=1)
    event, outbox_rows, run_rows = bundle
    typer.echo(_event_block(event))
    typer.echo("outbox:")
    if not outbox_rows:
        typer.echo("  (none)")
    for row in outbox_rows:
        typer.echo(
            f"  #{row.id} handler={row.handler} status={row.status}"
            f" attempt={row.attempt} epoch={row.attempt_epoch}"
            + (f" err={row.last_error}" if row.last_error else "")
        )
    typer.echo("runs:")
    if not run_rows:
        typer.echo("  (none)")
    for run in run_rows:
        typer.echo(
            f"  #{run.id} handler={run.handler} status={run.status}"
            f" started={run.started_at} dur={run.duration_ms}ms by={run.triggered_by}"
            + (f" err={run.error}" if run.error else "")
        )


async def _events_get(
    *, event_id: str, config_path: str | None
) -> tuple[queries.EventRecord, list[queries.OutboxRow], list[queries.RunRow]] | None:
    cfg = load(config_path)
    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        event = await queries.get_event(conn, event_id=event_id)
        if event is None:
            return None
        outbox_rows = await queries.outbox_for_event(conn, event_id=event_id)
        run_rows = await queries.runs_for_event(conn, event_id=event_id)
    return event, outbox_rows, run_rows


def _event_block(event: queries.EventRecord) -> str:
    return (
        f"event {event.id}\n"
        f"  type:        {event.type}\n"
        f"  source:      {event.source}\n"
        f"  dedup_key:   {event.source_dedup_key}\n"
        f"  trace_id:    {event.trace_id}\n"
        f"  created_at:  {event.created_at.isoformat()}\n"
        f"  payload:     {json.dumps(dict(event.payload), default=str, sort_keys=True)}"
    )


@triggers.command("ls", help="List configured triggers and quarantine status.")
def triggers_ls(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cfg = load(config)
    quarantined = asyncio.run(_quarantined_set(cfg))
    if not cfg.triggers:
        typer.echo("(no triggers configured)")
        return
    for name, entry in cfg.triggers.items():
        flag = (
            "QUARANTINED" if name in quarantined else ("enabled" if entry.enabled else "disabled")
        )
        typer.echo(f"  {name:14s} {flag}")


async def _quarantined_set(cfg: Config) -> set[str]:
    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        rows = await list_quarantined(conn)
    return {r["trigger_name"] for r in rows}


@triggers.command("unquarantine", help="Clear quarantine flag for one or more triggers.")
def triggers_unquarantine(
    names: list[str] = typer.Argument(..., help="Trigger names to clear."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cleared = asyncio.run(_unquarantine(names=names, config_path=config))
    typer.echo(f"cleared {cleared} quarantine row(s)")


async def _unquarantine(*, names: list[str], config_path: str | None) -> int:
    cfg = load(config_path)
    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        return await unquarantine(conn, trigger_names=names)


@handlers.command("ls", help="List configured handlers and their effective manifests.")
def handlers_ls(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cfg = load(config)
    registry = build_handler_registry(cfg)
    if not registry.by_name:
        typer.echo("(no handlers enabled)")
        return
    for name, record in registry.by_name.items():
        m = record.manifest
        typer.echo(
            f"  {name:14s} idempotent={m.idempotent}  ttl={m.dedup_ttl}"
            f"  conc={m.concurrency}  accepts={list(m.accepts)}"
        )
