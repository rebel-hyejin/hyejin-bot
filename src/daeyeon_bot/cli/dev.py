"""Dev commands: fire / call / repl. Not for production use."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import typer

from daeyeon_bot.app.config import load
from daeyeon_bot.core.events import make_event
from daeyeon_bot.infra import outbox, storage

app = typer.Typer(
    help="Developer utilities: fire triggers, call handlers, IPython repl.", no_args_is_help=True
)


@app.command(
    "fire",
    help="Fire a trigger to emit an event (writes through the normal outbox path).",
)
def fire(
    trigger: str,
    message: str = typer.Option("", "--message", "-m", help="Payload message field."),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    if trigger != "manual":
        raise typer.BadParameter(f"only 'manual' is supported in Phase 1, got {trigger!r}")
    asyncio.run(_fire_manual(message=message, config_path=config))


async def _fire_manual(*, message: str, config_path: str | None) -> None:
    cfg = load(config_path)
    cfg.state_dir_path.mkdir(parents=True, exist_ok=True)
    routing = cfg.routing.get("manual.message", [])
    if not routing:
        raise typer.BadParameter(
            "no handlers configured for 'manual.message'. Edit config.toml's [routing] section."
        )

    now = datetime.now(tz=UTC)
    event = make_event(type="manual.message", payload={"message": message}, created_at=now)
    dedup_key = f"cli-{uuid.uuid4()}"

    async with storage.connection(cfg.db_path) as conn:
        await storage.apply_migrations(conn)
        ok = await outbox.insert_event(conn, event, source="manual", source_dedup_key=dedup_key)
        if not ok:
            raise typer.Exit(code=1)
        for handler in routing:
            await outbox.enqueue_handler(conn, event_id=event.id, handler=handler, now=now)
        await conn.commit()
    typer.echo(event.id)


@app.command(
    "call",
    help="Invoke a handler directly with an event JSON, BYPASSING the outbox. Dev only.",
)
def call(handler: str, event_json: str = typer.Option(..., "--event-json")) -> None:
    raise NotImplementedError("Phase 3: bypass outbox, call handler.handle() with parsed event")


@app.command("repl", help="Drop into IPython with the production container bound.")
def repl() -> None:
    raise NotImplementedError("Phase 3: IPython.embed with container in scope")
