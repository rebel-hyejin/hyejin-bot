"""Tests for `dev fire-pr-review` (T027).

Covers the parser (URL + short-form), `--dry-run` JSON, the happy-path enqueue,
the empty-routing rejection, and the missing-head-SHA rejection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from daeyeon_bot.cli import dev as dev_cli
from daeyeon_bot.cli.dev import (
    _parse_pr_ref,  # pyright: ignore[reportPrivateUsage]
)

# ── Pure parser ──────────────────────────────────────────────────────────


def test_parse_short_form() -> None:
    assert _parse_pr_ref("rebellions-sw/daeyeon-bot#42") == ("rebellions-sw/daeyeon-bot", 42)


def test_parse_url_form() -> None:
    assert _parse_pr_ref("https://github.com/octo/cat/pull/7") == ("octo/cat", 7)


def test_parse_url_with_trailing_slash() -> None:
    assert _parse_pr_ref("https://github.com/octo/cat/pull/7/") == ("octo/cat", 7)


def test_parse_invalid_raises() -> None:
    import typer

    with pytest.raises(typer.BadParameter):
        _parse_pr_ref("not a ref")


# ── End-to-end via CliRunner ─────────────────────────────────────────────


class _FakeGhCli:
    """Minimal stand-in for `infra.gh_cli.GhCli` — only `pr_get` is used."""

    def __init__(self, head_sha: str = "deadbeefcafe1234") -> None:
        self._head_sha = head_sha
        self.calls: list[tuple[str, int]] = []

    async def pr_get(self, repo: str, pr_number: int) -> dict[str, Any]:
        self.calls.append((repo, pr_number))
        if not self._head_sha:
            return {"head": {}}
        return {"head": {"sha": self._head_sha}}


def _write_config(tmp_path: Path, *, with_routing: bool) -> Path:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    body = (
        f'[runtime]\nstate_dir = "{state_dir}"\n\n'
        '[github]\nusername = "daeyeon-lee"\n\n'
        '[handlers.pr_review]\npersona_skill = "pr-reviewer"\n\n'
    )
    if with_routing:
        body += '[routing]\n"pr.review.manual" = ["pr_review"]\n'
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    return cfg


def _patch_gh(monkeypatch: pytest.MonkeyPatch, fake: _FakeGhCli) -> None:
    def _factory(**_kwargs: object) -> _FakeGhCli:
        return fake

    monkeypatch.setattr(dev_cli, "GhCli", _factory)


def test_dry_run_prints_event_json_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGhCli(head_sha="abc123def4")
    _patch_gh(monkeypatch, fake)
    cfg = _write_config(tmp_path, with_routing=True)

    runner = CliRunner()
    result = runner.invoke(
        dev_cli.app,
        [
            "fire-pr-review",
            "--pr",
            "octo/cat#7",
            "--dry-run",
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code == 0, result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["type"] == "pr.review.manual"
    assert parsed["payload"]["repo"] == "octo/cat"
    assert parsed["payload"]["pr_number"] == 7
    assert parsed["payload"]["head_sha"] == "abc123def4"
    assert parsed["payload"]["force"] is False
    assert parsed["routes_to"] == ["pr_review"]
    assert fake.calls == [("octo/cat", 7)]
    # No state.db file should exist after a dry-run.
    assert not (tmp_path / "state" / "state.db").exists()


def test_no_routing_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGhCli()
    _patch_gh(monkeypatch, fake)
    cfg = _write_config(tmp_path, with_routing=False)

    runner = CliRunner()
    result = runner.invoke(
        dev_cli.app,
        [
            "fire-pr-review",
            "--pr",
            "octo/cat#7",
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code != 0
    assert "no handlers configured" in result.stdout.lower() + result.stderr.lower()
    # Aborted before calling gh.pr_get.
    assert fake.calls == []


def test_missing_head_sha_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGhCli(head_sha="")  # GitHub returned no SHA
    _patch_gh(monkeypatch, fake)
    cfg = _write_config(tmp_path, with_routing=True)

    runner = CliRunner()
    result = runner.invoke(
        dev_cli.app,
        [
            "fire-pr-review",
            "--pr",
            "octo/cat#7",
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code != 0
    assert "head sha" in (result.stdout + result.stderr).lower()


def test_happy_path_writes_event_and_outbox_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGhCli(head_sha="cafebabef00d")
    _patch_gh(monkeypatch, fake)
    cfg = _write_config(tmp_path, with_routing=True)

    runner = CliRunner()
    result = runner.invoke(
        dev_cli.app,
        [
            "fire-pr-review",
            "--pr",
            "octo/cat#7",
            "--force",
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    event_id = result.stdout.strip()
    assert event_id  # the new UUIDv7

    # Verify the event + outbox rows landed.
    import asyncio

    from daeyeon_bot.infra import storage

    async def _check() -> tuple[str, str, str]:
        async with storage.connection(tmp_path / "state" / "state.db") as conn:
            async with conn.execute(
                "SELECT id, type, payload_json FROM events WHERE id = ?", (event_id,)
            ) as cur:
                row = await cur.fetchone()
            assert row is not None
            return row["id"], row["type"], row["payload_json"]

    eid, etype, payload = asyncio.run(_check())
    assert eid == event_id
    assert etype == "pr.review.manual"
    parsed = json.loads(payload)
    assert parsed["force"] is True
    assert parsed["request_gen"].startswith("manual_")
