"""Dev commands: fire / call / repl. Not for production use."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import typer

from daeyeon_bot.app.config import load
from daeyeon_bot.app.registry import build_handler_registry
from daeyeon_bot.core.events import Event, make_event
from daeyeon_bot.core.results import Ack, DeadLetter, HandlerResult, Retry
from daeyeon_bot.core.time import Clock, SystemClock
from daeyeon_bot.infra import outbox, storage
from daeyeon_bot.infra.claude import FakeClaudeSession

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


@dataclass(slots=True)
class _DevHandlerCtx:
    """Concrete HandlerContext for ad-hoc dev invocations."""

    clock: Clock
    trace_id: str
    claude_session_factory: object


@app.command(
    "call",
    help="Invoke a handler directly with an event JSON, BYPASSING the outbox. Dev only.",
)
def call(
    handler: str,
    event_json: str = typer.Option(
        "",
        "--event-json",
        help="Full event JSON: {type, payload, ...}. Mutually exclusive with --message.",
    ),
    message: str = typer.Option(
        "",
        "--message",
        "-m",
        help="Shortcut: build a manual.message event with this payload.message.",
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    if bool(event_json) == bool(message):
        raise typer.BadParameter("specify exactly one of --event-json or --message")
    result = asyncio.run(
        _call_handler(
            handler_name=handler, event_json=event_json, message=message, config_path=config
        )
    )
    typer.echo(_render_result(result))


async def _call_handler(
    *, handler_name: str, event_json: str, message: str, config_path: str | None
) -> HandlerResult:
    cfg = load(config_path)
    registry = build_handler_registry(cfg)
    record = registry.by_name.get(handler_name)
    if record is None:
        raise typer.BadParameter(f"unknown or disabled handler: {handler_name!r}")

    event = _build_event(event_json=event_json, message=message)
    ctx = _DevHandlerCtx(
        clock=SystemClock(),
        trace_id=event.trace_id,
        claude_session_factory=lambda: FakeClaudeSession(default="dev-call"),
    )
    return await record.instance.handle(event, ctx)  # type: ignore[attr-defined,no-any-return]


def _build_event(*, event_json: str, message: str) -> Event:
    now = datetime.now(tz=UTC)
    if message:
        return make_event(type="manual.message", payload={"message": message}, created_at=now)
    try:
        raw: object = json.loads(event_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--event-json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise typer.BadParameter("--event-json must be a JSON object")
    data = cast("dict[str, object]", raw)
    event_type = data.get("type")
    if not isinstance(event_type, str):
        raise typer.BadParameter("--event-json must include a string 'type' field")
    payload_raw = data.get("payload", {})
    if not isinstance(payload_raw, dict):
        raise typer.BadParameter("--event-json 'payload' must be an object")
    payload = cast("dict[str, object]", payload_raw)
    return make_event(type=event_type, payload=payload, created_at=now)


def _render_result(result: HandlerResult) -> str:
    match result:
        case Ack():
            return "Ack"
        case Retry(after_s=after):
            return f"Retry(after_s={after})"
        case DeadLetter(reason=reason):
            return f"DeadLetter(reason={reason!r})"


@app.command("repl", help="Drop into IPython with the production container bound.")
def repl() -> None:
    raise NotImplementedError("Phase 3+: IPython.embed with container in scope")
