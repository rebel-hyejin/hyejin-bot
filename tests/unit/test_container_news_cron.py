"""Container wiring for feature 003 — news_deps / cron_deps gating.

The cron trigger fires events at a handler by name. If that handler isn't
wired (e.g. news enabled but Slack unavailable), wiring the cron anyway would
dead-letter every fire as "handler not registered". These tests pin the gate
that skips the cron in that case (Copilot PR #1 finding).
"""

from __future__ import annotations

from hyejin_bot.app.config import Config, CronTriggerEntry
from hyejin_bot.app.container import (
    _build_cron_deps,  # pyright: ignore[reportPrivateUsage]
)
from hyejin_bot.core.time import SystemClock


def _cron_cfg(*, handler: str = "news") -> Config:
    return Config(triggers={"cron": CronTriggerEntry(handler=handler)})


def test_cron_deps_skipped_when_target_handler_not_wired() -> None:
    # news_deps is None → "news" not in wired set → cron must not wire.
    deps = _build_cron_deps(config=_cron_cfg(), clock=SystemClock(), wired_handlers=set())
    assert deps is None


def test_cron_deps_built_when_target_handler_wired() -> None:
    deps = _build_cron_deps(config=_cron_cfg(), clock=SystemClock(), wired_handlers={"news"})
    assert deps is not None


def test_cron_deps_skipped_when_target_handler_is_a_different_unwired_name() -> None:
    # Cron targets "news" but only some other handler is wired → still skip.
    deps = _build_cron_deps(
        config=_cron_cfg(handler="news"), clock=SystemClock(), wired_handlers={"echo"}
    )
    assert deps is None


def test_cron_deps_skipped_when_cron_disabled() -> None:
    cfg = Config(triggers={"cron": CronTriggerEntry(enabled=False)})
    deps = _build_cron_deps(config=cfg, clock=SystemClock(), wired_handlers={"news"})
    assert deps is None
