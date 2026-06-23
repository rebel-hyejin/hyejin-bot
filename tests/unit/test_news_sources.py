"""news_sources — feature 003 tests for the pure filter/parse helpers.

The HTTP fetch itself is covered indirectly via the handler's FakeNewsFetcher;
here we exercise the score-threshold fallback and the GeekNews RSS parser,
which are the parts most likely to break on a real feed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from hyejin_bot.core.news.types import NewsItem, NewsSource
from hyejin_bot.infra.news_sources import (
    HttpNewsFetcher,
    _apply_hn_threshold,  # pyright: ignore[reportPrivateUsage]
    _parse_geeknews_rss,  # pyright: ignore[reportPrivateUsage]
)


def _hn(score: int) -> NewsItem:
    return NewsItem(
        source=NewsSource.HACKER_NEWS, title=f"s{score}", url=f"https://ex.com/{score}", score=score
    )


def test_hn_threshold_prefers_high_scores() -> None:
    items = [_hn(250), _hn(210), _hn(120), _hn(90)]
    kept = _apply_hn_threshold(items, limit=6)
    # Both >=200 kept, sorted desc; sub-200 dropped because the 200 bar held.
    assert [it.score for it in kept] == [250, 210]


def test_hn_threshold_falls_back_on_slow_day() -> None:
    items = [_hn(160), _hn(110), _hn(40)]
    kept = _apply_hn_threshold(items, limit=6)
    # Nothing clears 200; the 150 bar admits 160; 110/40 stay out.
    assert [it.score for it in kept] == [160]


def test_hn_threshold_returns_top_when_all_below_lowest_bar() -> None:
    items = [_hn(50), _hn(30)]
    kept = _apply_hn_threshold(items, limit=6)
    assert [it.score for it in kept] == [50, 30]


def test_hn_threshold_respects_limit() -> None:
    items = [_hn(300), _hn(290), _hn(280)]
    kept = _apply_hn_threshold(items, limit=2)
    assert [it.score for it in kept] == [300, 290]


_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>최신 글</title>
    <link>https://news.hada.io/topic?id=1</link>
    <pubDate>Tue, 23 Jun 2026 01:00:00 +0000</pubDate>
  </item>
  <item>
    <title>오래된 글</title>
    <link>https://news.hada.io/topic?id=2</link>
    <pubDate>Mon, 01 Jun 2026 00:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""


def test_geeknews_rss_keeps_recent_only() -> None:
    now = datetime(2026, 6, 23, 6, 0, 0, tzinfo=UTC)
    items = _parse_geeknews_rss(_RSS, limit=10, now=now, window_hours=24)
    assert len(items) == 1
    assert items[0].title == "최신 글"
    assert items[0].source is NewsSource.GEEKNEWS
    assert items[0].score is None


def test_geeknews_rss_respects_limit() -> None:
    now = datetime(2026, 6, 23, 6, 0, 0, tzinfo=UTC)
    items = _parse_geeknews_rss(_RSS, limit=1, now=now, window_hours=24 * 365)
    assert len(items) == 1


def test_geeknews_rss_malformed_returns_empty() -> None:
    now = datetime(2026, 6, 23, 6, 0, 0, tzinfo=UTC)
    assert _parse_geeknews_rss("<not xml", limit=10, now=now, window_hours=24) == []


_RSS_NAIVE_DATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>tz 없는 글</title>
    <link>https://news.hada.io/topic?id=9</link>
    <pubDate>Tue, 23 Jun 2026 01:00:00</pubDate>
  </item>
</channel></rss>
"""


def test_geeknews_rss_naive_pubdate_does_not_raise() -> None:
    # A pubDate without a timezone parses to a naive datetime; comparing it
    # against the tz-aware cutoff must NOT raise (Copilot finding). We treat
    # naive as UTC, so this item lands inside a 24h window.
    now = datetime(2026, 6, 23, 6, 0, 0, tzinfo=UTC)
    items = _parse_geeknews_rss(_RSS_NAIVE_DATE, limit=10, now=now, window_hours=24)
    assert len(items) == 1
    assert items[0].title == "tz 없는 글"


_RSS_NO_PUBDATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>날짜 없는 글</title>
    <link>https://news.hada.io/topic?id=7</link>
  </item>
</channel></rss>
"""


def test_geeknews_rss_drops_items_without_pubdate() -> None:
    # Contract: keep only items provably within the window. A missing/
    # unparseable pubDate has no provable recency → dropped (Copilot finding).
    now = datetime(2026, 6, 23, 6, 0, 0, tzinfo=UTC)
    assert _parse_geeknews_rss(_RSS_NO_PUBDATE, limit=10, now=now, window_hours=24) == []


def test_geeknews_rss_rejects_oversized_body() -> None:
    # A body far larger than the real feed is refused before parsing
    # (resource-exhaustion guard). Build a >4MB string cheaply.
    now = datetime(2026, 6, 23, 6, 0, 0, tzinfo=UTC)
    huge = "<rss>" + ("x" * (5 * 1024 * 1024)) + "</rss>"
    assert _parse_geeknews_rss(huge, limit=10, now=now, window_hours=24) == []


def _hn_transport() -> httpx.MockTransport:
    """Mock the HN Firebase API: topstories list + per-item JSON."""
    top = [101, 102, 103]
    items = {
        101: {"title": "First", "url": "https://ex.com/1", "score": 300},
        102: {"title": "Second", "url": "https://ex.com/2", "score": 250},
        # 103 is a self-post (no url) → dropped by the fetcher.
        103: {"title": "Ask HN: foo", "score": 280},
    }

    def _handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/topstories.json"):
            return httpx.Response(200, json=top)
        item_id = int(path.rsplit("/", 1)[-1].removesuffix(".json"))
        return httpx.Response(200, json=items[item_id])

    return httpx.MockTransport(_handler)


@pytest.mark.asyncio
async def test_http_fetch_hacker_news_concurrent_preserves_order_and_filters() -> None:
    # Real fetcher over a mocked transport: concurrent item fetch (asyncio.gather)
    # preserves topstories order and drops the self-post with no url.
    async with httpx.AsyncClient(transport=_hn_transport()) as client:
        fetcher = HttpNewsFetcher(http_client=client)
        items = await fetcher.fetch_hacker_news(limit=6)
    assert [it.title for it in items] == ["First", "Second"]
    assert all(it.source is NewsSource.HACKER_NEWS for it in items)
