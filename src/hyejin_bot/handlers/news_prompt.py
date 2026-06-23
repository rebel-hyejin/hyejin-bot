"""Prompt construction for the news handler's Claude call.

The system prompt fixes the persona (hyejin's DevOps/k8s/AI lens) and the
strict JSON output contract; the user message lists the HN items to summarize.
GeekNews items are excluded — they're Korean-native and shipped title-only.
"""

from __future__ import annotations

import json

from hyejin_bot.core.news.types import NewsItem

_SYSTEM_PROMPT = """\
You curate a daily tech-news clip for hyejin, a DevOps engineer on an NPU \
chip company's System Software team (CI/CD pipelines, Kubernetes, runner \
fleets, IaC, AI infra). For each Hacker News item below, write a summary \
aimed at "why this matters to her work", not a literal translation.

Output ONLY a JSON object, no prose, matching exactly:
{
  "summaries": [
    {
      "url": "<the item's url, verbatim>",
      "headline_ko": "<one Korean line, aim for ~40 chars, hard max 120, the 'why it matters' angle>",
      "bullets_en": ["<English bullet, <=20 words>", "<=3 bullets total>"]
    }
  ]
}

Rules:
- One entry per item given, keyed by the exact `url` passed in.
- `headline_ko` is Korean. `bullets_en` are English, concrete, <=20 words each.
- If you only have the title (no article body), still give a best-effort \
headline and at most one bullet noting the angle — never invent specifics.
- Do not add items that weren't given. Do not drop items.
"""


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT


def build_user_message(hn_items: list[NewsItem]) -> str:
    """Render the HN items as a compact JSON list for Claude to summarize."""
    payload = [{"url": it.url, "title": it.title, "score": it.score} for it in hn_items]
    return (
        "Summarize these Hacker News items. Return the JSON object described "
        "in the system prompt.\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


__all__ = ["build_system_prompt", "build_user_message"]
