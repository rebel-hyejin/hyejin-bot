"""Hot SQLite backup + retention pruning of older snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from daeyeon_bot.app.backup import BACKUP_DIR_NAME, run_backup
from daeyeon_bot.infra import storage


async def _seed_db(db_path: Path) -> None:
    conn = await storage.open_db(db_path)
    try:
        await storage.apply_migrations(conn)
        await conn.execute(
            """
            INSERT INTO events(id, type, source, source_dedup_key, payload_json,
                               trace_id, created_at, schema_version)
            VALUES ('e1','manual.message','manual','k','{}','t','2026-01-01T00:00:00+00:00',1)
            """
        )
        await conn.commit()
    finally:
        await conn.close()


async def test_backup_creates_snapshot_with_data(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await _seed_db(db_path)
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)

    report = await run_backup(db_path=db_path, state_dir=tmp_path, keep=5, now=now)

    assert report.snapshot_path.exists()
    assert report.snapshot_path.parent.name == BACKUP_DIR_NAME
    assert report.snapshot_path.stat().st_mode & 0o777 == 0o600

    snap_conn = await aiosqlite.connect(report.snapshot_path)
    try:
        async with snap_conn.execute("SELECT id FROM events") as cur:
            rows = await cur.fetchall()
    finally:
        await snap_conn.close()
    assert [row[0] for row in rows] == ["e1"]


async def test_backup_prunes_to_keep_newest(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await _seed_db(db_path)
    base = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)

    snapshots: list[Path] = []
    for i in range(5):
        report = await run_backup(
            db_path=db_path,
            state_dir=tmp_path,
            keep=3,
            now=base + timedelta(seconds=i),
        )
        snapshots.append(report.snapshot_path)

    backup_dir = tmp_path / BACKUP_DIR_NAME
    surviving = sorted(backup_dir.glob("state-*.db"))
    assert len(surviving) == 3
    # The newest three should remain.
    assert surviving == sorted(snapshots[-3:])


async def test_backup_keep_no_prune_when_under_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await _seed_db(db_path)
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    report = await run_backup(db_path=db_path, state_dir=tmp_path, keep=10, now=now)
    assert report.pruned == ()
