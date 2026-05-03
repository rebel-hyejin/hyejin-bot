"""Ops commands: doctor / migrate / replay / prune."""

from __future__ import annotations

import asyncio

import typer

from daeyeon_bot.app.config import load
from daeyeon_bot.infra import storage

app = typer.Typer(
    help="Operations: pre-flight checks, schema migrations, replay, prune.", no_args_is_help=True
)


@app.command(
    "doctor",
    help="Run pre-flight checks: token, DB, config, pending migrations, disk, heartbeat staleness.",
)
def doctor() -> None:
    raise NotImplementedError("Phase 3: implement doctor checks per docs/PLAN.md §5 Phase 3")


@app.command("migrate", help="Apply pending SQLite schema migrations under a transaction.")
def migrate(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    asyncio.run(_migrate(config_path=config))


async def _migrate(*, config_path: str | None) -> None:
    cfg = load(config_path)
    cfg.state_dir_path.mkdir(parents=True, exist_ok=True)
    async with storage.connection(cfg.db_path) as conn:
        version = await storage.apply_migrations(conn)
    typer.echo(f"schema_version={version}")


@app.command("replay", help="Re-emit a dead-lettered or processed event (attempt_epoch++).")
def replay(
    event_id: str,
    handler: str | None = typer.Option(None, "--handler", help="Replay only this handler."),
    confirm: bool = typer.Option(False, "--confirm", help="Required to actually re-emit."),
) -> None:
    raise NotImplementedError("Phase 3: dry-run by default, --confirm to bump attempt_epoch")


@app.command("prune", help="Apply retention: events 90d, runs 30d, dedup expired, last 5 backups.")
def prune() -> None:
    raise NotImplementedError("Phase 6: enforce retention policy from config")
