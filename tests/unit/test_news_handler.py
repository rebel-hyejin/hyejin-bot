"""news handler — feature 003 tests.

Wires the handler with a FakeNewsFetcher, FakeClaudeSession, and
FakeSlackClient (no network, no DB) and asserts:
  * the clip is fetched, summarized, rendered, and DM'd in order;
  * a malformed Claude response still ships a title-only clip (best-effort);
  * an empty fetch ships a "no news" DM rather than erroring;
  * a missing slack_channel is a ValidationError.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from hyejin_bot.core.errors import ValidationError
from hyejin_bot.core.events import make_event
from hyejin_bot.core.news.types import NewsItem, NewsSource
from hyejin_bot.core.results import Ack
from hyejin_bot.core.time import Clock, SystemClock
from hyejin_bot.handlers.news import MANIFEST, NewsHandler
from hyejin_bot.infra.claude import FakeClaudeSession, FakeFactory
from hyejin_bot.infra.news_sources import FakeNewsFetcher
from hyejin_bot.infra.slack import FakeSlackClient


@dataclass(slots=True)
class _Ctx:
    clock: Clock
    trace_id: str
    claude_session_factory: Callable[[], object]


def _event() -> object:
    return make_event(
        type="news.daily",
        payload={"job": "news_daily"},
        created_at=datetime(2026, 6, 23, 0, 0, 0, tzinfo=UTC),
    )


def _hn(title: str, url: str, score: int) -> NewsItem:
    return NewsItem(source=NewsSource.HACKER_NEWS, title=title, url=url, score=score)


def _gn(title: str, url: str) -> NewsItem:
    return NewsItem(source=NewsSource.GEEKNEWS, title=title, url=url)


def _make_handler(
    *, fetcher: FakeNewsFetcher, claude: FakeClaudeSession, slack: FakeSlackClient, channel: str
) -> NewsHandler:
    return NewsHandler(
        manifest=MANIFEST,
        fetcher=fetcher,
        slack=slack,
        slack_channel=channel,
        timezone_name="Asia/Seoul",
        hn_limit=6,
        geeknews_limit=4,
    )


def _ctx(claude: FakeClaudeSession) -> _Ctx:
    return _Ctx(clock=SystemClock(), trace_id="t-1", claude_session_factory=FakeFactory(claude))


@pytest.mark.asyncio
async def test_full_clip_fetched_summarized_and_dmd() -> None:
    fetcher = FakeNewsFetcher(
        hn_items=[_hn("Rust 2.0", "https://ex.com/rust", 530)],
        geeknews_items=[_gn("GeekNews 글", "https://news.hada.io/1")],
    )
    summary_json = json.dumps(
        {
            "summaries": [
                {
                    "url": "https://ex.com/rust",
                    "headline_ko": "러스트가 빌드 파이프라인에 주는 영향",
                    "bullets_en": ["Faster builds", "Backward compatible"],
                }
            ]
        }
    )
    claude = FakeClaudeSession(responses=[summary_json])
    slack = FakeSlackClient()
    handler = _make_handler(fetcher=fetcher, claude=claude, slack=slack, channel="D08GP012483")

    result = await handler.handle(_event(), _ctx(claude))  # type: ignore[arg-type]

    assert isinstance(result, Ack)
    assert fetcher.calls == ["hn", "geeknews"]
    assert len(slack.calls) == 1
    text = slack.calls[0]["text"]
    assert slack.calls[0]["channel"] == "D08GP012483"
    # Date is the handler's clock "today" — assert the date-agnostic header so
    # the test doesn't rot when the calendar rolls over.
    assert "뉴스 클립" in text
    assert "Rust 2.0" in text
    assert "러스트가 빌드 파이프라인에 주는 영향" in text
    assert "• Faster builds" in text
    assert "GeekNews 글" in text
    assert "https://news.hada.io/1" in text


@pytest.mark.asyncio
async def test_malformed_claude_ships_title_only_clip() -> None:
    fetcher = FakeNewsFetcher(hn_items=[_hn("Story", "https://ex.com/a", 300)])
    # Two malformed responses → both retry attempts fail → empty summaries.
    claude = FakeClaudeSession(responses=["not json", "still not json"])
    slack = FakeSlackClient()
    handler = _make_handler(fetcher=fetcher, claude=claude, slack=slack, channel="D1")

    result = await handler.handle(_event(), _ctx(claude))  # type: ignore[arg-type]

    assert isinstance(result, Ack)
    assert len(slack.calls) == 1
    text = slack.calls[0]["text"]
    assert "Story" in text  # title still shipped
    assert "https://ex.com/a" in text
    # No headline/bullets since summarization failed.
    assert "•" not in text


@pytest.mark.asyncio
async def test_empty_fetch_ships_no_news_message() -> None:
    fetcher = FakeNewsFetcher(hn_items=[], geeknews_items=[])
    claude = FakeClaudeSession(responses=[])
    slack = FakeSlackClient()
    handler = _make_handler(fetcher=fetcher, claude=claude, slack=slack, channel="D1")

    result = await handler.handle(_event(), _ctx(claude))  # type: ignore[arg-type]

    assert isinstance(result, Ack)
    assert len(slack.calls) == 1
    assert "뉴스가 없었어요" in slack.calls[0]["text"]
    # No HN items → Claude not called.
    assert claude.calls == []


@pytest.mark.asyncio
async def test_missing_channel_is_validation_error() -> None:
    fetcher = FakeNewsFetcher(hn_items=[_hn("x", "https://ex.com/x", 300)])
    claude = FakeClaudeSession()
    slack = FakeSlackClient()
    handler = _make_handler(fetcher=fetcher, claude=claude, slack=slack, channel="")

    with pytest.raises(ValidationError):
        await handler.handle(_event(), _ctx(claude))  # type: ignore[arg-type]
