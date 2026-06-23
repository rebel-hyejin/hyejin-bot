"""Fetch + filter raw news items from Hacker News and GeekNews.

Mirrors the standalone `news` skill's collection step (see
`~/.claude/skills/news/SKILL.md`) but as a daemon-side adapter:

    * Hacker News — `topstories.json` then per-item `item/<id>.json` on the
      public Firebase API. Filter to `score >= threshold`, dropping the
      threshold (200 → 150 → 100) on a slow news day so the clip is never
      empty. Self-posts with no external `url` (some Ask/Show HN) are dropped.
    * GeekNews — the FeedBurner RSS feed; keep items published within the
      last `geeknews_window_hours`.

Error policy follows `infra/loki.py`: a transport/parse failure on ONE
source returns that source's items as empty rather than raising, so a HN
outage still ships the GeekNews half (and vice versa). Only a total wipe-out
is the handler's problem to notice.

The `NewsFetcher` Protocol lets the handler stay testable — unit tests inject
a `FakeNewsFetcher` with scripted items instead of hitting the network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, cast, runtime_checkable

import httpx
import structlog

from hyejin_bot.core.news.types import NewsItem, NewsSource

_log = structlog.get_logger(__name__)

_HN_TOPSTORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
_GEEKNEWS_RSS = "https://feeds.feedburner.com/geeknews-feed"

# HN score thresholds tried in order — stop at the first that yields any item.
_HN_SCORE_THRESHOLDS: tuple[int, ...] = (200, 150, 100)
# How many of the top-ranked HN stories to inspect per run.
_HN_TOP_SCAN = 50


@runtime_checkable
class NewsFetcher(Protocol):
    """Surface the news handler depends on. Returns already-filtered items."""

    async def fetch_hacker_news(self, *, limit: int) -> list[NewsItem]: ...

    async def fetch_geeknews(self, *, limit: int, now: datetime) -> list[NewsItem]: ...


@dataclass(frozen=True, slots=True)
class HttpNewsFetcher:
    """Real fetcher over the public HN Firebase API + GeekNews RSS."""

    timeout_s: float = 10.0
    geeknews_window_hours: int = 24
    http_client: httpx.AsyncClient | None = field(default=None)

    async def fetch_hacker_news(self, *, limit: int) -> list[NewsItem]:
        client = self.http_client or httpx.AsyncClient(timeout=self.timeout_s)
        owns = self.http_client is None
        try:
            try:
                ids = await self._get_json(client, _HN_TOPSTORIES)
            except (httpx.HTTPError, ValueError) as exc:
                _log.warning("news.hn_topstories_failed", error=str(exc))
                return []
            if not isinstance(ids, list):
                return []
            id_list = cast("list[Any]", ids)
            items: list[NewsItem] = []
            for raw_id in id_list[:_HN_TOP_SCAN]:
                if not isinstance(raw_id, int):
                    continue
                item = await self._fetch_hn_item(client, raw_id)
                if item is not None:
                    items.append(item)
            return _apply_hn_threshold(items, limit=limit)
        finally:
            if owns:
                await client.aclose()

    async def fetch_geeknews(self, *, limit: int, now: datetime) -> list[NewsItem]:
        client = self.http_client or httpx.AsyncClient(timeout=self.timeout_s)
        owns = self.http_client is None
        try:
            try:
                resp = await client.get(_GEEKNEWS_RSS)
                resp.raise_for_status()
                body = resp.text
            except httpx.HTTPError as exc:
                _log.warning("news.geeknews_fetch_failed", error=str(exc))
                return []
            return _parse_geeknews_rss(
                body, limit=limit, now=now, window_hours=self.geeknews_window_hours
            )
        finally:
            if owns:
                await client.aclose()

    async def _fetch_hn_item(self, client: httpx.AsyncClient, item_id: int) -> NewsItem | None:
        try:
            data = await self._get_json(client, _HN_ITEM.format(id=item_id))
        except (httpx.HTTPError, ValueError) as exc:
            _log.warning("news.hn_item_failed", item_id=item_id, error=str(exc))
            return None
        if not isinstance(data, dict):
            return None
        item = cast("dict[str, Any]", data)
        url = item.get("url")
        title = item.get("title")
        score = item.get("score")
        # Drop self-posts (Ask HN / some Show HN) — no external link to share.
        if not isinstance(url, str) or not url:
            return None
        if not isinstance(title, str) or not title:
            return None
        return NewsItem(
            source=NewsSource.HACKER_NEWS,
            title=title,
            url=url,
            score=score if isinstance(score, int) else None,
        )

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Any:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _apply_hn_threshold(items: list[NewsItem], *, limit: int) -> list[NewsItem]:
    """Keep items at the highest score threshold that still yields any, capped to `limit`."""
    ranked = sorted(items, key=lambda it: it.score or 0, reverse=True)
    for threshold in _HN_SCORE_THRESHOLDS:
        kept = [it for it in ranked if (it.score or 0) >= threshold]
        if kept:
            return kept[:limit]
    # Nothing cleared even the lowest bar — return the top few by score anyway
    # rather than an empty HN half on a very slow day.
    return ranked[:limit]


def _parse_geeknews_rss(
    body: str, *, limit: int, now: datetime, window_hours: int
) -> list[NewsItem]:
    try:
        root = ET.fromstring(body)  # noqa: S314 — trusted feed, not user input
    except ET.ParseError as exc:
        _log.warning("news.geeknews_parse_failed", error=str(exc))
        return []
    cutoff = now - timedelta(hours=window_hours)
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = _rss_text(item, "title")
        link = _rss_text(item, "link")
        if not title or not link:
            continue
        pub = _rss_text(item, "pubDate")
        published = _parse_rss_date(pub)
        if published is not None and published < cutoff:
            continue
        out.append(NewsItem(source=NewsSource.GEEKNEWS, title=title, url=link))
        if len(out) >= limit:
            break
    return out


def _rss_text(item: ET.Element, tag: str) -> str | None:
    el = item.find(tag)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _parse_rss_date(value: str | None) -> datetime | None:
    """Parse an RFC 822 `pubDate`, always returning a tz-aware datetime.

    `parsedate_to_datetime` returns a *naive* datetime when the header omits a
    timezone (some feeds do). Comparing a naive value against the tz-aware
    `cutoff` raises `TypeError` and would break the whole GeekNews half, so we
    assume UTC for naive results — close enough for a 24h recency window.
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(slots=True)
class FakeNewsFetcher:
    """Test double — returns scripted items; records calls."""

    hn_items: list[NewsItem] = field(default_factory=list[NewsItem])
    geeknews_items: list[NewsItem] = field(default_factory=list[NewsItem])
    calls: list[str] = field(default_factory=list[str])

    async def fetch_hacker_news(self, *, limit: int) -> list[NewsItem]:
        self.calls.append("hn")
        return self.hn_items[:limit]

    async def fetch_geeknews(self, *, limit: int, now: datetime) -> list[NewsItem]:
        del now
        self.calls.append("geeknews")
        return self.geeknews_items[:limit]


__all__ = ["FakeNewsFetcher", "HttpNewsFetcher", "NewsFetcher"]
