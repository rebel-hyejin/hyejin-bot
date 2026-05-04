"""Audit-row dataclass for `pr_review_audit` (data-model.md §3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One row in `pr_review_audit`.

    `request_gen` is a string for the same reason as `PullRequestRef`: auto
    rows hold the int as text, manual `--force` rows hold `"manual_<ts>"`.
    """

    id: int
    event_id: str
    repo: str
    pr_number: int
    head_sha: str
    request_gen: str
    status: str
    review_id: int | None
    submitted_at: datetime | None
    summary_chars: int | None
    inline_comment_count: int | None
    superseded_review_ids: tuple[int, ...]
    persona_skill: str | None
    persona_mtime_ns: int | None
    error: str | None
    created_at: datetime
