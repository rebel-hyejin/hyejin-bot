"""Lifecycle commands: run / pause / resume / stop / setup-secret."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import keyring
import keyring.errors
import typer

from daeyeon_bot.app import pause as pause_mod
from daeyeon_bot.app.config import Config, load
from daeyeon_bot.app.lifecycle import AlreadyRunningError, BootOptions, boot
from daeyeon_bot.core.errors import AuthError, ConfigError

app = typer.Typer(help="Lifecycle controls: pause, resume, stop.", no_args_is_help=True)


def run(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
    insecure_env: bool = typer.Option(
        False,
        "--insecure-env",
        help="Allow secrets.provider='env' (token visible in /proc on Linux).",
    ),
) -> None:
    """Start the daemon (foreground). Use launchd / systemd in production."""
    try:
        asyncio.run(boot(BootOptions(config_path=config, insecure_env_allowed=insecure_env)))
    except AlreadyRunningError as exc:
        typer.echo(f"daeyeon-bot: {exc}", err=True)
        # 75 = EX_TEMPFAIL — supervisor (launchd/systemd) should retry later.
        raise typer.Exit(code=75) from exc
    except (AuthError, ConfigError) as exc:
        typer.echo(f"daeyeon-bot: {type(exc).__name__}: {exc}", err=True)
        # 78 = EX_CONFIG — operator must rotate the token / fix config.
        raise typer.Exit(code=78) from exc


@app.command("pause", help="Create the PAUSE flag; running handlers continue, new ones block.")
def pause(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cfg = load(config)
    cfg.state_dir_path.mkdir(parents=True, exist_ok=True)
    created = pause_mod.pause(cfg.pause_flag_path)
    if created:
        typer.echo(f"paused: {cfg.pause_flag_path}")
    else:
        typer.echo(f"already paused: {cfg.pause_flag_path}")


@app.command("resume", help="Remove the PAUSE flag; new handlers may proceed.")
def resume(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    cfg = load(config)
    removed = pause_mod.resume(cfg.pause_flag_path)
    if removed:
        typer.echo(f"resumed: removed {cfg.pause_flag_path}")
    else:
        typer.echo(f"not paused: {cfg.pause_flag_path} did not exist")


@app.command("stop", help="Send SIGTERM to the running daemon (pidfile lookup).")
def stop(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    _send_pidfile_signal(config, signal.SIGTERM, action="SIGTERM")


@app.command(
    "reload-config",
    help=(
        "Restart the daemon so config.toml is re-read. Persona file edits "
        "(mtime change) take effect on the next event without this — only "
        "use this when [handlers.pr_review].persona_skill itself changes."
    ),
)
def reload_config(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    _send_pidfile_signal(config, signal.SIGTERM, action="reload (SIGTERM)")
    typer.echo(
        "supervisor (launchd/systemd KeepAlive) will start a fresh daemon with the new config",
        err=True,
    )


@app.command(
    "setup-secret",
    help=(
        "Stash a named secret via the configured `[secrets].provider`."
        " Prompts for the value (hidden). Use for: jira_user, jira_api_token,"
        " ssw_automation_password, and any other named secret the daemon"
        " reads via `load_secret(<name>)`. The OAuth token uses a separate"
        " path (`scripts/setup-token.sh`)."
    ),
)
def setup_secret(
    name: str = typer.Argument(
        ...,
        help=(
            "Snake-case secret name (e.g. 'jira_user'). Must match what the"
            " caller passes to `secrets_provider.load_secret(name)`."
        ),
    ),
    value: str = typer.Option(
        "",
        "--value",
        help=(
            "Provide the secret on the command line instead of via prompt."
            " UNSAFE on a shared shell — prefer the interactive prompt."
        ),
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.toml."),
) -> None:
    """Stash a named secret. `[secrets].provider` determines where it lands."""
    cfg = load(config)
    if not _is_valid_secret_name(name):
        typer.echo(
            f"setup-secret: invalid name {name!r} — must be snake_case"
            " (lowercase letters, digits, underscores; no slashes or dots)",
            err=True,
        )
        raise typer.Exit(code=2)

    secret = (
        value
        if value
        else typer.prompt(f"value for {name!r}", hide_input=True, confirmation_prompt=False)
    )
    if not secret:
        typer.echo("setup-secret: empty value rejected", err=True)
        raise typer.Exit(code=2)

    provider = cfg.secrets.provider
    if provider == "keychain":
        _stash_keychain(service=cfg.secrets.keychain_service, account=name, value=secret)
        typer.echo(f"stashed: keychain service={cfg.secrets.keychain_service!r} account={name!r}")
    elif provider == "file":
        path = _stash_file(cfg=cfg, name=name, value=secret)
        typer.echo(f"stashed: {path} (mode 0o600)")
    elif provider == "env":
        typer.echo(
            "setup-secret: provider='env' cannot be stashed from a running"
            " process. Export the env var in your shell / systemd unit /"
            " launchd plist instead. Expected name: " + name.upper(),
            err=True,
        )
        raise typer.Exit(code=2)
    else:
        typer.echo(f"setup-secret: unknown provider {provider!r}", err=True)
        raise typer.Exit(code=2)


def _stash_keychain(*, service: str, account: str, value: str) -> None:
    try:
        keyring.set_password(service, account, value)
    except keyring.errors.KeyringError as exc:
        typer.echo(f"setup-secret: keychain backend error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _stash_file(*, cfg: Config, name: str, value: str) -> Path:
    """Write `<secrets_dir>/<name>` with 0o600. Returns the resolved path.

    `<secrets_dir>` is the parent of `[secrets].file_path` (the OAuth file).
    The named secret lives as a sibling — matches `FileSecrets.load_secret`.
    """
    file_path = Path(cfg.secrets.file_path).expanduser()
    secrets_dir = file_path.parent
    secrets_dir.mkdir(parents=True, exist_ok=True)
    target = secrets_dir / name
    # Create with 0o600 BEFORE writing so the value never lives on disk with
    # looser perms even briefly.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(target, flags, 0o600)
    try:
        os.write(fd, (value + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    # Defensive: enforce 0o600 even if the file existed with looser perms.
    target.chmod(0o600)
    return target


def _is_valid_secret_name(name: str) -> bool:
    """Path-traversal + shape guard. Must match what FileSecrets.load_secret accepts."""
    if not name:
        return False
    if "/" in name or ".." in name or name.startswith("."):
        return False
    if not all(c.islower() or c.isdigit() or c == "_" for c in name):
        return False
    return True


def _send_pidfile_signal(config: str | None, sig: signal.Signals, *, action: str) -> None:
    cfg = load(config)
    pidfile = cfg.pidfile_path
    if not pidfile.exists():
        typer.echo(f"no pidfile at {pidfile}", err=True)
        raise typer.Exit(code=1)
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        typer.echo(f"unreadable pidfile {pidfile}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        typer.echo(f"no process with pid {pid} (stale pidfile?)", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{action} sent to pid {pid}")
