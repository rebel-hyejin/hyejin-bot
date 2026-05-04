"""Unit tests for `handlers.pr_review_diff` (T023)."""

from __future__ import annotations

from daeyeon_bot.handlers.pr_review_diff import (
    is_anchor_in_hunk,
    parse_hunk_ranges,
)


def test_parse_single_hunk() -> None:
    patch = "@@ -1,3 +5,4 @@\n context\n+added\n+added\n context\n"
    assert parse_hunk_ranges(patch) == [(5, 8)]


def test_parse_multiple_hunks() -> None:
    patch = "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n@@ -10,2 +12,3 @@\n d\n+e\n f\n"
    assert parse_hunk_ranges(patch) == [(1, 3), (12, 14)]


def test_parse_default_count_when_omitted() -> None:
    # `+5` with no `,N` defaults to a 1-line hunk → (5, 5).
    patch = "@@ -1 +5 @@\n+only-one\n"
    assert parse_hunk_ranges(patch) == [(5, 5)]


def test_parse_empty_post_change_hunk() -> None:
    # `+0,0` is the delete-only case: impossible interval → `end < start`.
    patch = "@@ -10,3 +0,0 @@\n-removed\n-removed\n-removed\n"
    ranges = parse_hunk_ranges(patch)
    assert ranges == [(0, -1)]
    # Nothing can anchor inside it.
    assert is_anchor_in_hunk(0, None, ranges) is False
    assert is_anchor_in_hunk(1, None, ranges) is False


def test_parse_no_hunks_in_empty_patch() -> None:
    assert parse_hunk_ranges("") == []
    assert parse_hunk_ranges("just some context\nno hunks here\n") == []


def test_anchor_inside_hunk_accepted() -> None:
    hunks = [(5, 8)]
    assert is_anchor_in_hunk(5, None, hunks) is True
    assert is_anchor_in_hunk(7, None, hunks) is True
    assert is_anchor_in_hunk(8, None, hunks) is True


def test_anchor_outside_hunk_rejected() -> None:
    hunks = [(5, 8)]
    assert is_anchor_in_hunk(4, None, hunks) is False
    assert is_anchor_in_hunk(9, None, hunks) is False


def test_multi_line_anchor_inside_one_hunk_accepted() -> None:
    hunks = [(1, 3), (12, 14)]
    assert is_anchor_in_hunk(line=14, start_line=12, hunks=hunks) is True
    assert is_anchor_in_hunk(line=3, start_line=1, hunks=hunks) is True


def test_multi_line_anchor_spanning_hunks_rejected() -> None:
    hunks = [(1, 3), (12, 14)]
    assert is_anchor_in_hunk(line=14, start_line=3, hunks=hunks) is False
    assert is_anchor_in_hunk(line=12, start_line=1, hunks=hunks) is False


def test_inverted_range_rejected() -> None:
    hunks = [(5, 8)]
    # start_line > line is malformed Claude output → reject.
    assert is_anchor_in_hunk(line=5, start_line=8, hunks=hunks) is False


def test_empty_hunks_list_rejects_everything() -> None:
    assert is_anchor_in_hunk(1, None, []) is False
    assert is_anchor_in_hunk(line=1, start_line=1, hunks=[]) is False
