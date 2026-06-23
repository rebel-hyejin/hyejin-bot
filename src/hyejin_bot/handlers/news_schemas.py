"""Pydantic schemas for the news handler's Claude round-trip.

Claude is asked to summarize the HN half of the clip: per item, a one-line
Korean "why it matters" headline plus up to three concise English bullets.
GeekNews items are NOT summarized (Korean-native, title + link only), so they
never appear in this schema — the handler renders them directly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HnSummary(BaseModel):
    """Summary for one Hacker News item, keyed back by `url`."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    headline_ko: str = Field(min_length=1, max_length=120)
    bullets_en: list[str] = Field(default_factory=list, max_length=3)


class NewsSummaryOutput(BaseModel):
    """Top-level object Claude returns: one entry per HN item it was given."""

    model_config = ConfigDict(extra="forbid")

    summaries: list[HnSummary] = Field(default_factory=list)
