"""news_render — feature 003 tests for clip rendering + Slack-limit splitting.

Focus on the message-packing contract Copilot flagged: every emitted chunk
must carry the header, and we must never ship a header-only message even when
the first block alone would overflow beside the header.
"""

from __future__ import annotations

from hyejin_bot.core.news.types import NewsItem, NewsSource
from hyejin_bot.handlers.news_render import render_messages
from hyejin_bot.handlers.news_schemas import HnSummary

_LIMIT = 4000


def _hn(title: str, url: str, score: int) -> NewsItem:
    return NewsItem(source=NewsSource.HACKER_NEWS, title=title, url=url, score=score)


def test_single_message_under_limit() -> None:
    msgs = render_messages(
        date_str="2026-06-23",
        hn_items=[_hn("A", "https://ex.com/a", 300)],
        geeknews_items=[],
        summaries={},
    )
    assert len(msgs) == 1
    assert "2026-06-23 뉴스 클립" in msgs[0]
    assert "A" in msgs[0]


def test_empty_clip_is_no_news_message() -> None:
    msgs = render_messages(date_str="2026-06-23", hn_items=[], geeknews_items=[], summaries={})
    assert len(msgs) == 1
    assert "뉴스가 없었어요" in msgs[0]


def test_long_clip_splits_and_every_message_keeps_header() -> None:
    # Each HN item carries a long summary so the clip overflows 4000 chars
    # across several blocks.
    items = [_hn(f"Story {i}", f"https://ex.com/{i}", 300 + i) for i in range(12)]
    summaries = {
        it.url: HnSummary(
            url=it.url,
            headline_ko="중요한 이유 " + "가" * 60,
            bullets_en=["x" * 90, "y" * 90, "z" * 90],
        )
        for it in items
    }
    msgs = render_messages(
        date_str="2026-06-23", hn_items=items, geeknews_items=[], summaries=summaries
    )

    assert len(msgs) > 1  # actually split
    for m in msgs:
        assert len(m) <= _LIMIT
        assert "2026-06-23 뉴스 클립" in m  # header on every chunk
    # No message is header-only — each chunk has at least one story block.
    for m in msgs:
        assert "Story" in m


def test_single_oversized_block_is_truncated_to_limit() -> None:
    # A single item whose rendered block alone exceeds 4000 chars must be
    # truncated so the emitted message still honors the Slack limit (no
    # silent reliance on Slack-side truncation — Copilot finding).
    big = _hn("T", "https://ex.com/big", 300)
    summaries = {
        big.url: HnSummary(
            url=big.url,
            headline_ko="h",
            bullets_en=["q" * 4500],  # block > 4000 on its own
        )
    }
    msgs = render_messages(
        date_str="2026-06-23", hn_items=[big], geeknews_items=[], summaries=summaries
    )
    assert all(len(m) <= _LIMIT for m in msgs)
    assert any("…(생략)" in m for m in msgs)


def test_split_covers_all_items_in_order() -> None:
    items = [_hn(f"Story{i}", f"https://ex.com/{i}", 300 + i) for i in range(10)]
    summaries = {
        it.url: HnSummary(url=it.url, headline_ko="요" * 80, bullets_en=["b" * 95] * 3)
        for it in items
    }
    msgs = render_messages(
        date_str="2026-06-23", hn_items=items, geeknews_items=[], summaries=summaries
    )
    joined = "\n".join(msgs)
    for i in range(10):
        assert f"Story{i}" in joined
