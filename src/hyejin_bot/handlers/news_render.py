"""Render the fetched + summarized news into Slack message(s).

Format mirrors `~/.claude/skills/news/SKILL.md`:

    📰 *YYYY-MM-DD 뉴스 클립* — HN N건 · GeekNews M건
    ━━━━━━━━━━━━━━━━━━━━━━━━━━
    *1. <title>* `HN, 530점`
    _왜 중요한가 (한 문장)_
    • bullet 1
    • bullet 2
    <url>
    ...

Slack caps a single `chat.postMessage` `text` at ~4000 chars; `split_message`
chunks on item boundaries so a long clip ships as several messages instead of
being silently truncated.
"""

from __future__ import annotations

from hyejin_bot.core.news.types import NewsItem, NewsSource
from hyejin_bot.handlers.news_schemas import HnSummary

_DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
_SLACK_TEXT_LIMIT = 4000


def render_messages(
    *,
    date_str: str,
    hn_items: list[NewsItem],
    geeknews_items: list[NewsItem],
    summaries: dict[str, HnSummary],
) -> list[str]:
    """Build the clip and split it into <=4000-char Slack messages.

    `summaries` maps HN item url → its Claude summary. Items missing a
    summary still render (title + score + link) so a partial Claude result
    never drops a story.
    """
    header = f"📰 *{date_str} 뉴스 클립* — HN {len(hn_items)}건 · GeekNews {len(geeknews_items)}건"

    blocks: list[str] = []
    index = 1
    for item in hn_items:
        blocks.append(_render_hn_block(index, item, summaries.get(item.url)))
        index += 1
    for item in geeknews_items:
        blocks.append(_render_geeknews_block(index, item))
        index += 1

    if not blocks:
        return [f"{header}\n\n{_DIVIDER}\n\n오늘은 임계값을 넘는 뉴스가 없었어요."]

    return _pack(header, blocks)


def _render_hn_block(index: int, item: NewsItem, summary: HnSummary | None) -> str:
    score_tag = f"`HN, {item.score}점`" if item.score is not None else "`HN`"
    lines = [f"*{index}. {item.title}* {score_tag}"]
    if summary is not None:
        if summary.headline_ko:
            lines.append(f"_{summary.headline_ko}_")
        lines.extend(f"• {b}" for b in summary.bullets_en if b)
    lines.append(item.url)
    return "\n".join(lines)


def _render_geeknews_block(index: int, item: NewsItem) -> str:
    return f"*{index}. {item.title}* `{NewsSource.GEEKNEWS.value}`\n{item.url}"


def _pack(header: str, blocks: list[str]) -> list[str]:
    """Greedily pack header + blocks into <=_SLACK_TEXT_LIMIT messages."""
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{_DIVIDER}\n\n{block}"
        if len(candidate) <= _SLACK_TEXT_LIMIT:
            current = candidate
            continue
        messages.append(current)
        # Start a fresh message. A single block over the limit is rare; ship it
        # on its own (Slack will truncate it, but it stays the only casualty).
        current = block
    messages.append(current)
    return messages


__all__ = ["render_messages"]
