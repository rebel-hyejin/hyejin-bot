"""`inspect pr-review` CLI smoke test (T041).

Drives the Typer app against a tmp config + a real `aiosqlite` state.db
seeded with a handful of audit rows. Verifies the two modes:

* No flags → shows recent rows across all PRs.
* `--pr o/r#7` → shows that PR's history (newest first) only.
* `--pr` parser rejects malformed specs with `BadParameter`.

Sync test functions: the CLI itself uses `asyncio.run()` internally, which
deadlocks under `pytest-asyncio` auto-mode. Seeding runs in a one-shot
`asyncio.run()` before each invoke.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from daeyeon_bot.cli import inspect as inspect_cli
from daeyeon_bot.infra import storage
from daeyeon_bot.infra.pr_review_audit import insert_audit


def _write_config(tmp_path: Path) -> Path:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[runtime]\nstate_dir = "{state_dir}"\n', encoding="utf-8")
    return cfg


async def _seed_pr7_history(db_path: Path) -> None:
    async with storage.connection(db_path) as conn:
        await storage.apply_migrations(conn)
        await _insert_event(conn, "evt-a")
        await _insert_event(conn, "evt-b")
        await _insert_event(conn, "evt-c")
        now = datetime.now(tz=UTC)
        await insert_audit(
            conn,
            event_id="evt-a",
            repo="octo/cat",
            pr_number=7,
            head_sha="aaaa1111",
            request_gen="1",
            status="posted",
            review_id=101,
            persona_skill="pr-reviewer",
            persona_mtime_ns=10,
            submitted_at=now,
            created_at=now,
        )
        await insert_audit(
            conn,
            event_id="evt-b",
            repo="octo/cat",
            pr_number=7,
            head_sha="bbbb2222",
            request_gen="2",
            status="posted",
            review_id=202,
            persona_skill="pr-reviewer",
            persona_mtime_ns=20,
            submitted_at=now,
            created_at=now,
        )
        await insert_audit(
            conn,
            event_id="evt-c",
            repo="octo/cat",
            pr_number=99,
            head_sha="cccc3333",
            request_gen="1",
            status="skipped_self_authored",
            persona_skill="pr-reviewer",
            persona_mtime_ns=20,
            created_at=now,
        )
        await conn.commit()


async def _seed_two_prs(db_path: Path) -> None:
    async with storage.connection(db_path) as conn:
        await storage.apply_migrations(conn)
        await _insert_event(conn, "evt-1")
        await _insert_event(conn, "evt-2")
        now = datetime.now(tz=UTC)
        await insert_audit(
            conn,
            event_id="evt-1",
            repo="o/r",
            pr_number=1,
            head_sha="aaa",
            request_gen="1",
            status="posted",
            review_id=1,
            submitted_at=now,
            created_at=now,
        )
        await insert_audit(
            conn,
            event_id="evt-2",
            repo="o/r",
            pr_number=2,
            head_sha="bbb",
            request_gen="1",
            status="posted",
            review_id=2,
            submitted_at=now,
            created_at=now,
        )
        await conn.commit()


async def _insert_event(conn: object, event_id: str) -> None:
    from aiosqlite import Connection

    assert isinstance(conn, Connection)
    await conn.execute(
        "INSERT INTO events(id, type, schema_version, source, source_dedup_key,"
        " payload_json, trace_id, created_at)"
        " VALUES (?, 'pr.review.manual', 1, 'manual', ?, '{}', 'tr', ?)",
        (event_id, f"k-{event_id}", "2026-05-04T00:00:00+00:00"),
    )


def test_inspect_pr_review_filters_by_pr(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    db_path = tmp_path / "state" / "state.db"
    asyncio.run(_seed_pr7_history(db_path))

    runner = CliRunner()
    result = runner.invoke(
        inspect_cli.app, ["pr-review", "--pr", "octo/cat#7", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    idx_202 = out.find("review=202")
    idx_101 = out.find("review=101")
    assert idx_202 != -1 and idx_101 != -1
    assert idx_202 < idx_101
    assert "skipped_self_authored" not in out


def test_inspect_pr_review_no_flags_lists_recent(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    db_path = tmp_path / "state" / "state.db"
    asyncio.run(_seed_two_prs(db_path))

    runner = CliRunner()
    result = runner.invoke(inspect_cli.app, ["pr-review", "--config", str(cfg)])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "review=1" in out
    assert "review=2" in out


def test_inspect_pr_review_rejects_malformed_pr_spec(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        inspect_cli.app, ["pr-review", "--pr", "not-a-spec", "--config", str(cfg)]
    )
    assert result.exit_code != 0
    combined = (result.stdout + (result.stderr or "")).lower()
    assert "owner/repo#n" in combined


def test_inspect_pr_review_no_rows_message(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(inspect_cli.app, ["pr-review", "--config", str(cfg)])
    assert result.exit_code == 0, result.stdout
    assert "(no audit rows)" in result.stdout
