"""Handler registry: instantiation, manifest overrides, routing lookup."""

from __future__ import annotations

import pytest

from hyejin_bot.app.config import (
    Config,
    CronTriggerEntry,
    HandlerEntry,
    NewsHandlerEntry,
    PrReviewHandlerEntry,
)
from hyejin_bot.app.registry import (
    CronDeps,
    NewsDeps,
    PrReviewDeps,
    build_handler_registry,
    build_trigger_registry,
)
from hyejin_bot.core.errors import ConfigError
from hyejin_bot.core.time import SystemClock
from hyejin_bot.handlers.news import NewsHandler
from hyejin_bot.handlers.pr_review import PrReviewHandler
from hyejin_bot.infra.news_sources import FakeNewsFetcher
from hyejin_bot.infra.pr_review_persona import PersonaLoader
from hyejin_bot.infra.slack import FakeSlackClient
from hyejin_bot.triggers.cron import CronTrigger


def test_echo_registered_with_default_manifest() -> None:
    cfg = Config(
        handlers={"echo": HandlerEntry()},
        routing={"manual.message": ["echo"]},
    )
    registry = build_handler_registry(cfg)
    record = registry.by_name["echo"]
    assert record.manifest.idempotent is True
    assert record.manifest.dedup_ttl.days == 1
    assert registry.handlers_for("manual.message") == [record]
    assert registry.handlers_for("unknown") == []


def test_handler_disabled_means_unregistered() -> None:
    cfg = Config(
        handlers={"echo": HandlerEntry(enabled=False)},
        routing={"manual.message": ["echo"]},
    )
    registry = build_handler_registry(cfg)
    assert "echo" not in registry.by_name


def test_manifest_overrides_apply() -> None:
    cfg = Config(
        handlers={
            "echo": HandlerEntry(
                concurrency=4,
                dedup_ttl_seconds=3600,
                accepts=["custom.type"],
            )
        },
        routing={"custom.type": ["echo"]},
    )
    registry = build_handler_registry(cfg)
    manifest = registry.by_name["echo"].manifest
    assert manifest.concurrency == 4
    assert manifest.dedup_ttl.total_seconds() == 3600
    assert manifest.accepts == ("custom.type",)


def test_unknown_handler_name_raises() -> None:
    cfg = Config(handlers={"nonexistent": HandlerEntry()})
    with pytest.raises(ConfigError):
        build_handler_registry(cfg)


def _pr_review_cfg() -> Config:
    return Config(
        handlers={"pr_review": PrReviewHandlerEntry(persona_skill="pr-reviewer")},
        routing={"pr.review.manual": ["pr_review"]},
    )


def test_pr_review_skipped_when_no_deps_provided() -> None:
    """Inspection-only callers (e.g. `inspect handlers ls`) pass no deps."""
    registry = build_handler_registry(_pr_review_cfg())
    assert "pr_review" not in registry.by_name


def test_pr_review_registered_when_deps_provided(tmp_path: object) -> None:
    deps = PrReviewDeps(
        gh=object(),
        persona_loader=PersonaLoader(),
        db=object(),
        github_username="hyejin-lee",
    )
    registry = build_handler_registry(_pr_review_cfg(), pr_review_deps=deps)
    record = registry.by_name["pr_review"]
    assert isinstance(record.instance, PrReviewHandler)
    assert record.manifest.name == "pr_review"
    assert "gh.review_requested" in record.manifest.accepts
    assert "pr.review.manual" in record.manifest.accepts


def test_pr_review_directly_instantiated_without_deps_raises() -> None:
    """Mismatched call: enabled=True but no deps must surface a clear error."""
    from hyejin_bot.app.registry import instantiate_handler

    with pytest.raises(ConfigError, match="PrReviewDeps"):
        instantiate_handler("pr_review", PrReviewHandlerEntry())


# ── News handler + cron trigger registration (feature 003) ────────────────────


def _news_cfg() -> Config:
    return Config(
        handlers={"news": NewsHandlerEntry(accepts=["news.daily"])},
        routing={"news.daily": ["news"]},
    )


def test_news_skipped_when_no_deps_provided() -> None:
    """Inspection-only callers pass no deps → handler is skipped."""
    registry = build_handler_registry(_news_cfg())
    assert "news" not in registry.by_name


def test_news_registered_when_deps_provided() -> None:
    deps = NewsDeps(
        fetcher=FakeNewsFetcher(),
        slack=FakeSlackClient(),
        slack_channel="D08GP012483",
    )
    registry = build_handler_registry(_news_cfg(), news_deps=deps)
    record = registry.by_name["news"]
    assert isinstance(record.instance, NewsHandler)
    assert record.manifest.accepts == ("news.daily",)


def test_cron_trigger_skipped_when_no_deps_provided() -> None:
    cfg = Config(triggers={"cron": CronTriggerEntry()})
    triggers = build_trigger_registry(cfg)
    assert all(t.name != "cron" for t in triggers)


def test_cron_trigger_registered_when_deps_provided() -> None:
    cfg = Config(triggers={"cron": CronTriggerEntry()})

    async def _never(_reason: str) -> bool:
        return False

    deps = CronDeps(
        storage_factory=lambda: None,  # type: ignore[arg-type, return-value]
        clock=SystemClock(),
        pause_check=lambda: False,
        permanent_failure_reporter=_never,
    )
    triggers = build_trigger_registry(cfg, cron_deps=deps)
    cron = next(t for t in triggers if t.name == "cron")
    assert isinstance(cron.instance, CronTrigger)
    assert cron.instance.event_type == "news.daily"
