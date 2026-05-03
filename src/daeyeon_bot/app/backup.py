"""Hot SQLite backup using `Connection.backup()`.

Snapshots land under `<state_dir>/backups/state-<UTC ISO>.db`. We then
prune the directory to keep at most `retention.backup_keep` files (newest
first). The backup is safe to run while the daemon is live — SQLite's
backup API copies pages incrementally without blocking writers for long.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiosqlite

BACKUP_DIR_NAME = "backups"
BACKUP_FILENAME_FMT = "state-{stamp}.db"


@dataclass(frozen=True, slots=True)
class BackupReport:
    snapshot_path: Path
    pruned: tuple[Path, ...]


async def run_backup(*, db_path: Path, state_dir: Path, keep: int, now: datetime) -> BackupReport:
    """Take a hot snapshot and prune older backups beyond `keep`."""
    snapshot_path = await _snapshot(db_path=db_path, state_dir=state_dir, now=now)
    pruned = await asyncio.to_thread(_prune_old_backups, state_dir, keep)
    return BackupReport(snapshot_path=snapshot_path, pruned=tuple(pruned))


async def _snapshot(*, db_path: Path, state_dir: Path, now: datetime) -> Path:
    backup_dir = state_dir / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = backup_dir / BACKUP_FILENAME_FMT.format(stamp=_stamp(now))

    src = await aiosqlite.connect(db_path)
    try:
        # The destination is plain sqlite3 — aiosqlite.backup accepts both.
        dst = sqlite3.connect(snapshot_path)
        try:
            await src.backup(dst)
        finally:
            dst.close()
    finally:
        await src.close()
    snapshot_path.chmod(0o600)
    return snapshot_path


def _prune_old_backups(state_dir: Path, keep: int) -> list[Path]:
    backup_dir = state_dir / BACKUP_DIR_NAME
    if not backup_dir.is_dir():
        return []
    snapshots = sorted(backup_dir.glob("state-*.db"))
    if len(snapshots) <= keep:
        return []
    to_remove = snapshots[: len(snapshots) - keep]
    for path in to_remove:
        path.unlink(missing_ok=True)
    return to_remove


def _stamp(now: datetime) -> str:
    """Filename-safe ISO timestamp (no colons, UTC, second precision)."""
    return now.strftime("%Y%m%dT%H%M%SZ")


__all__ = ["BACKUP_DIR_NAME", "BackupReport", "run_backup"]
