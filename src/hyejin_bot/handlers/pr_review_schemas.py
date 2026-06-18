"""Pydantic v2 schema for the JSON Claude must produce per `contracts/claude-review-output.md` §1.

`extra="forbid"` is the load-bearing knob: a hallucinated key like
`"approve": true` must NOT silently sneak into a future API call.

The `verdict` field also drives the GitHub Review API `event` we submit
with: `APPROVE` becomes a real GitHub APPROVE review (counts toward
branch protection). The validator enforces consistency — a verdict of
APPROVE is only allowed when `comments[]` is empty, so the bot can't
self-contradict by approving while attaching findings.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class InlineComment(BaseModel):
    """One inline review comment anchored to a line in the post-change diff."""

    model_config = {"extra": "forbid"}

    path: str = Field(min_length=1, max_length=512)
    line: int = Field(ge=1)
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    start_line: int | None = Field(default=None, ge=1)
    body: str = Field(min_length=1, max_length=8000)


Verdict = Literal["APPROVE", "PASS", "CONCERNS", "FAIL"]


class ReviewOutput(BaseModel):
    """Top-level Claude output: a Verdict + Summary + zero or more inline comments."""

    model_config = {"extra": "forbid"}

    verdict: Verdict
    summary: str = Field(min_length=1, max_length=2500)
    comments: list[InlineComment] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def verdict_matches_comments(self) -> ReviewOutput:
        """APPROVE means zero findings — handler will submit GH event=APPROVE.

        - `APPROVE` ↔ `comments == []`
        - `PASS` allowed regardless of comment count (semantic: MINOR-only)
        - `CONCERNS` / `FAIL` typically carry comments but we don't gate
          structurally — severity isn't visible in the schema, only in
          the inline body text.
        """
        if self.verdict == "APPROVE" and self.comments:
            raise ValueError(
                "verdict=APPROVE requires comments to be empty"
                f" (got {len(self.comments)} inline comments)"
            )
        return self


__all__ = ["InlineComment", "ReviewOutput", "Verdict"]
