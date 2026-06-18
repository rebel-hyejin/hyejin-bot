"""Pure domain dataclasses for PR-review handling. Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """Identifier of a single review request instance.

    `request_gen` is a string so the same field carries both the auto-trigger's
    monotonic int (rendered as `"1"`, `"2"`, ...) and the manual sentinels
    (`"0"` for non-force, `"manual_<unix_ts>"` for force re-review).
    """

    repo: str
    pr_number: int
    head_sha: str
    request_gen: str


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """One changed file in a PR. `patch` is None for binary or oversized files."""

    path: str
    additions: int
    deletions: int
    status: str
    patch: str | None


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    """Everything the handler hands to Claude for a single review."""

    ref: PullRequestRef
    title: str
    body: str
    author_login: str
    requested_reviewer_logins: tuple[str, ...]
    files: tuple[ChangedFile, ...]


@dataclass(frozen=True, slots=True)
class InlineCommentDraft:
    """One inline comment Claude proposed; anchor must fall in a diff hunk."""

    path: str
    line: int
    body: str
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    start_line: int | None = None


@dataclass(frozen=True, slots=True)
class ReviewDraft:
    """Validated Claude output, ready to ship to GitHub."""

    summary: str
    comments: tuple[InlineCommentDraft, ...]


@dataclass(frozen=True, slots=True)
class PostedReview:
    """The result GitHub returned for a successful POST /reviews."""

    review_id: int
    submitted_at: datetime
