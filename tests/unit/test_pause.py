"""PAUSE kill-switch file ops."""

from __future__ import annotations

from pathlib import Path

from hyejin_bot.app import pause


def test_is_paused_false_when_missing(tmp_path: Path) -> None:
    flag = tmp_path / "PAUSE"
    assert pause.is_paused(flag) is False


def test_pause_creates_flag(tmp_path: Path) -> None:
    flag = tmp_path / "subdir" / "PAUSE"
    assert pause.pause(flag) is True
    assert flag.exists()
    assert pause.is_paused(flag) is True


def test_pause_idempotent_returns_false_second_time(tmp_path: Path) -> None:
    flag = tmp_path / "PAUSE"
    assert pause.pause(flag) is True
    assert pause.pause(flag) is False


def test_resume_clears_flag(tmp_path: Path) -> None:
    flag = tmp_path / "PAUSE"
    pause.pause(flag)
    assert pause.resume(flag) is True
    assert not flag.exists()


def test_resume_returns_false_when_no_flag(tmp_path: Path) -> None:
    flag = tmp_path / "PAUSE"
    assert pause.resume(flag) is False


def test_pause_uses_owner_only_perms(tmp_path: Path) -> None:
    flag = tmp_path / "PAUSE"
    pause.pause(flag)
    mode = flag.stat().st_mode & 0o777
    assert mode == 0o600
