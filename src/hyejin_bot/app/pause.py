"""PAUSE kill-switch.

Operator drops `~/.hyejin-bot/PAUSE` to stop new handler dispatches without
killing the daemon. The dispatcher consults `is_paused()` before claiming a
new outbox row; in-flight handlers are not interrupted.

`pause()` / `resume()` are the file-side operations the CLI uses.
"""

from __future__ import annotations

from pathlib import Path


def is_paused(flag_path: Path) -> bool:
    """True iff the PAUSE flag file exists at `flag_path`."""
    return flag_path.exists()


def pause(flag_path: Path) -> bool:
    """Create the PAUSE flag. Returns True if newly created, False if already present."""
    if flag_path.exists():
        return False
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch(mode=0o600)
    return True


def resume(flag_path: Path) -> bool:
    """Remove the PAUSE flag. Returns True if removed, False if it wasn't there."""
    if not flag_path.exists():
        return False
    flag_path.unlink()
    return True


__all__ = ["is_paused", "pause", "resume"]
