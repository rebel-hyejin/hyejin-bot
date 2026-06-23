"""Pure domain types for the news clip. No I/O, stdlib only."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class NewsSource(enum.Enum):
    """Where an item came from. Drives summarization + render rules."""

    HACKER_NEWS = "HN"
    GEEKNEWS = "GeekNews"


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One fetched story, pre-summary.

    `score` is HN points; None for GeekNews (which has no public score).
    `url` is the external article link — self-posts without an external URL
    are dropped by the fetcher, so `url` is always a real link here.
    """

    source: NewsSource
    title: str
    url: str
    score: int | None = None
