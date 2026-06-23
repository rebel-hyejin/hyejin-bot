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
    """Greedily pack header + blocks into <=_SLACK_TEXT_LIMIT messages.

    Every emitted message starts with the header (with a `(N/N)`-style
    continuation hint added by the caller is unnecessary — the header repeats
    so each chunk has context). A message is only flushed once it carries at
    least one block, so we never ship a header-only message: when the very
    first block won't fit beside the header, the block becomes the start of a
    fresh `header + block` message rather than orphaning the header.
    """
    messages: list[str] = []
    current = header
    has_block = False
    for block in blocks:
        candidate = f"{current}\n\n{_DIVIDER}\n\n{block}"
        if len(candidate) <= _SLACK_TEXT_LIMIT:
            current = candidate
            has_block = True
            continue
        # Current message is full. Flush it only if it already holds a block;
        # otherwise it's header-only — keep building on it so the header isn't
        # shipped alone.
        if has_block:
            messages.append(current)
        # Start the next message with the header again so every chunk has
        # context. If a single block still overflows `header + block`, truncate
        # the block so the emitted message honors the <=_SLACK_TEXT_LIMIT
        # contract (no silent reliance on Slack-side truncation).
        prefix = f"{header}\n\n{_DIVIDER}\n\n"
        current = prefix + _truncate(block, _SLACK_TEXT_LIMIT - len(prefix))
        has_block = True
    messages.append(current)
    return messages


_TRUNCATE_MARKER = "…(생략)"


def _truncate(text: str, limit: int) -> str:
    """Clip `text` to at most `limit` chars, appending a marker when cut.

    `limit` is the budget left for the block after the header+divider prefix;
    it's comfortably positive for any realistic header, but we guard the
    degenerate case (limit <= marker length) by returning a hard slice.
    """
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATE_MARKER):
        return text[:limit]
    return text[: limit - len(_TRUNCATE_MARKER)] + _TRUNCATE_MARKER


__all__ = ["render_messages"]
