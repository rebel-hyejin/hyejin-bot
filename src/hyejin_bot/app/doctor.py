"""Pre-flight checks: token / DB / config / migrations / disk / heartbeat.

The CLI (`hyejin-bot ops doctor`) calls `run_checks(config)` and renders the
report. Every check returns a `CheckResult` with a status (`ok` / `warn` /
`fail`) and a one-line detail. Failures don't prevent later checks from
running — the operator wants the full picture in one shot.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiosqlite

from hyejin_bot.app.config import Config
from hyejin_bot.app.heartbeat import DEFAULT_TICK_S, staleness_seconds
from hyejin_bot.core.errors import AuthError, ConfigError
from hyejin_bot.infra import secrets, storage

CheckStatus = Literal["ok", "warn", "fail"]
DISK_WARN_BYTES = 100 * 1024 * 1024  # 100 MiB
DISK_FAIL_BYTES = 10 * 1024 * 1024  # 10 MiB


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    results: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(r.status != "fail" for r in self.results)


async def run_checks(config: Config) -> DoctorReport:
    """Execute the full check suite. Order: cheap → expensive."""
    results: list[CheckResult] = [
        _check_state_dir(config.state_dir_path),
        _check_disk(config.state_dir_path),
        _check_heartbeat(config.state_dir_path / "heartbeat"),
        _check_pause_flag(config.pause_flag_path),
        await _check_db_and_migrations(config.db_path),
        _check_token(config),
    ]
    return DoctorReport(results=tuple(results))


def _check_state_dir(state_dir: Path) -> CheckResult:
    name = "state_dir"
    if not state_dir.exists():
        return CheckResult(name=name, status="warn", detail=f"missing: {state_dir}")
    if not state_dir.is_dir():
        return CheckResult(name=name, status="fail", detail=f"not a directory: {state_dir}")
    return CheckResult(name=name, status="ok", detail=str(state_dir))


def _check_disk(state_dir: Path) -> CheckResult:
    name = "disk"
    target = state_dir if state_dir.exists() else state_dir.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return CheckResult(name=name, status="warn", detail=f"unreadable: {exc}")
    free_mb = usage.free // (1024 * 1024)
    if usage.free < DISK_FAIL_BYTES:
        return CheckResult(name=name, status="fail", detail=f"only {free_mb} MiB free")
    if usage.free < DISK_WARN_BYTES:
        return CheckResult(name=name, status="warn", detail=f"low: {free_mb} MiB free")
    return CheckResult(name=name, status="ok", detail=f"{free_mb} MiB free")


def _check_heartbeat(flag_path: Path) -> CheckResult:
    name = "heartbeat"
    age = staleness_seconds(flag_path, now_ts=time.time())
    if age is None:
        return CheckResult(name=name, status="warn", detail="no heartbeat file (daemon offline?)")
    if age > DEFAULT_TICK_S * 3:
        return CheckResult(name=name, status="fail", detail=f"stale: {int(age)}s old")
    return CheckResult(name=name, status="ok", detail=f"fresh ({int(age)}s ago)")


def _check_pause_flag(flag_path: Path) -> CheckResult:
    name = "pause"
    if flag_path.exists():
        return CheckResult(name=name, status="warn", detail=f"PAUSE active: {flag_path}")
    return CheckResult(name=name, status="ok", detail="not paused")


async def _check_db_and_migrations(db_path: Path) -> CheckResult:
    name = "db"
    if not await asyncio.to_thread(db_path.exists):
        return CheckResult(name=name, status="warn", detail=f"missing: {db_path} (run ops migrate)")
    try:
        async with storage.connection(db_path) as conn:
            integrity = await _integrity_check(conn)
            if integrity != "ok":
                return CheckResult(name=name, status="fail", detail=f"integrity: {integrity}")
            current = await _schema_version(conn)
            latest = _latest_migration_seq()
    except aiosqlite.Error as exc:
        return CheckResult(name=name, status="fail", detail=f"open failed: {exc}")
    if current < latest:
        return CheckResult(
            name=name,
            status="warn",
            detail=f"schema_version={current}, pending up to {latest} (run ops migrate)",
        )
    return CheckResult(name=name, status="ok", detail=f"schema_version={current}")


def _check_token(config: Config) -> CheckResult:
    """Probe the configured secrets provider and report success/failure.

    The token itself is never logged — only its length and the provider name.
    """
    name = "token"
    try:
        provider = secrets.build_provider(
            name=config.secrets.provider,
            keychain_service=config.secrets.keychain_service,
            keychain_account=config.secrets.keychain_account,
            file_path=config.secrets.file_path,
        )
        token = provider.load_oauth_token()
    except ConfigError as exc:
        return CheckResult(name=name, status="fail", detail=f"config: {exc}")
    except AuthError as exc:
        return CheckResult(name=name, status="fail", detail=f"unavailable: {exc}")
    return CheckResult(
        name=name,
        status="ok",
        detail=f"provider={config.secrets.provider} (token len={len(token)})",
    )


async def _integrity_check(conn: aiosqlite.Connection) -> str:
    async with conn.execute("PRAGMA integrity_check") as cur:
        row = await cur.fetchone()
    if row is None:
        return "no result"
    return str(row[0])


async def _schema_version(conn: aiosqlite.Connection) -> int:
    try:
        async with conn.execute("SELECT value FROM meta WHERE key='schema_version'") as cur:
            row = await cur.fetchone()
    except aiosqlite.OperationalError:
        return 0
    return int(row["value"]) if row is not None else 0


def _latest_migration_seq() -> int:
    """Return the highest migration sequence number bundled in this build."""
    files = storage.migration_files()
    return files[-1][0] if files else 0


__all__ = [
    "CheckResult",
    "CheckStatus",
    "DoctorReport",
    "run_checks",
]
