"""Daily news-clip handler (feature 003).

Consumes the `news.daily` event (emitted by the `cron` trigger, or fired
manually via the CLI) and:

    (a) fetch HN top stories + GeekNews RSS via the `NewsFetcher` adapter
    (b) cap to the configured per-source limits
    (c) summarize the HN half with Claude (Korean headline + EN bullets),
        retrying once on malformed output; GeekNews ships title-only
    (d) render the clip into <=4000-char Slack message(s)
    (e) DM the operator's channel, posting each message in order

The handler is idempotent: the cron trigger's `(job, local-date)` dedup key
makes a same-day re-emit a no-op upstream, and a manual re-fire just sends a
fresh clip. A Slack outage raises `TransientError` (from `HttpSlackClient`),
so the dispatcher retries the whole event — acceptable because the fetch is
cheap and the clip is regenerated fresh.

Failure policy:
    * Fetch returning zero items from BOTH sources → Ack with a "no news"
      DM (not an error — a genuinely quiet morning).
    * Claude malformed twice → still ship the clip with title-only HN blocks
      rather than dropping the whole DM (the summaries are a nice-to-have).
    * Slack send failure → propagates (Transient/Auth) for dispatcher retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from zoneinfo import ZoneInfo

import structlog
from pydantic import ValidationError as PydanticValidationError

from hyejin_bot.core.errors import ValidationError
from hyejin_bot.core.events import Event
from hyejin_bot.core.manifest import HandlerManifest
from hyejin_bot.core.news.types import NewsItem
from hyejin_bot.core.protocols import HandlerContext
from hyejin_bot.core.results import Ack, HandlerResult
from hyejin_bot.handlers.news_prompt import build_system_prompt, build_user_message
from hyejin_bot.handlers.news_render import render_messages
from hyejin_bot.handlers.news_schemas import HnSummary, NewsSummaryOutput
from hyejin_bot.infra.news_sources import NewsFetcher
from hyejin_bot.infra.slack import SlackClient

_log = structlog.get_logger(__name__)

MANIFEST = HandlerManifest(
    name="news",
    idempotent=True,
    dedup_ttl=timedelta(hours=23),
    side_effect_key="news_daily_dm",
    concurrency=1,
    accepts=("news.daily",),
)


@dataclass(slots=True)
class NewsHandler:
    """Fetch → summarize → DM the daily tech-news clip."""

    manifest: HandlerManifest
    fetcher: NewsFetcher
    slack: SlackClient
    slack_channel: str
    timezone_name: str = "Asia/Seoul"
    hn_limit: int = 6
    geeknews_limit: int = 4

    async def handle(self, event: Event, ctx: HandlerContext) -> HandlerResult:
        if not self.slack_channel:
            raise ValidationError("news handler requires a non-empty slack_channel")

        now = ctx.clock.now()
        date_str = now.astimezone(ZoneInfo(self.timezone_name)).date().isoformat()

        hn_items = await self.fetcher.fetch_hacker_news(limit=self.hn_limit)
        geeknews_items = await self.fetcher.fetch_geeknews(limit=self.geeknews_limit, now=now)

        _log.info(
            "news.fetched",
            event_id=event.id,
            trace_id=ctx.trace_id,
            hn=len(hn_items),
            geeknews=len(geeknews_items),
        )

        summaries = await self._summarize_hn(ctx, hn_items)
        messages = render_messages(
            date_str=date_str,
            hn_items=hn_items,
            geeknews_items=geeknews_items,
            summaries=summaries,
        )

        for text in messages:
            await self.slack.post_message(channel=self.slack_channel, text=text)

        _log.info(
            "news.sent",
            event_id=event.id,
            trace_id=ctx.trace_id,
            messages=len(messages),
            channel=self.slack_channel,
        )
        return Ack()

    async def _summarize_hn(
        self, ctx: HandlerContext, hn_items: list[NewsItem]
    ) -> dict[str, HnSummary]:
        """Summarize HN items via Claude. Best-effort: an unparseable result
        after one retry yields an empty map (title-only render), never a raise.
        """
        if not hn_items:
            return {}
        system_prompt = build_system_prompt()
        user_message = build_user_message(hn_items)
        last_error: str | None = None
        for attempt in (0, 1):
            session = ctx.claude_session_factory()
            async with session as s:  # type: ignore[attr-defined]
                response_obj = await s.query(  # type: ignore[attr-defined]
                    user_message
                    if last_error is None
                    else (
                        user_message
                        + "\n\n---\nYour previous response failed validation: "
                        + last_error
                        + "\nReturn ONLY the corrected JSON object."
                    ),
                    system=system_prompt,
                )
            response = cast("str", response_obj)
            try:
                parsed = json.loads(_strip_code_fence(response))
                output = NewsSummaryOutput.model_validate(parsed)
            except (TypeError, ValueError, PydanticValidationError) as exc:
                last_error = str(exc)
                if attempt == 1:
                    _log.warning("news.summary_unparseable", error=str(exc))
                    return {}
                continue
            return {s.url: s for s in output.summaries}
        return {}


def _strip_code_fence(text: str) -> str:
    """Tolerate Claude wrapping its JSON in ```json … ``` despite the prompt."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


__all__ = ["MANIFEST", "NewsHandler"]
