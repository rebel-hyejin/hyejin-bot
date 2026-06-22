"""Unit tests for the gh_cli filter helpers used by list_all_pr_comments."""

from __future__ import annotations

from typing import Any

from hyejin_bot.infra.gh_cli import (
    _filter_actors,  # pyright: ignore[reportPrivateUsage]
    _filter_reviews,  # pyright: ignore[reportPrivateUsage]
)


def test_filter_actors_drops_excluded_login() -> None:
    payload: list[Any] = [
        {"user": {"login": "rebel-hyejin"}, "body": "self"},
        {"user": {"login": "rebel-daeyeonlee"}, "body": "other"},
        {"user": {"login": "Copilot"}, "body": "ai"},
    ]
    out = _filter_actors(payload, exclude_login="rebel-hyejin")
    assert len(out) == 2
    assert {c["user"]["login"] for c in out} == {"rebel-daeyeonlee", "Copilot"}


def test_filter_actors_empty_exclude_keeps_all() -> None:
    payload: list[Any] = [
        {"user": {"login": "a"}, "body": "1"},
        {"user": {"login": "b"}, "body": "2"},
    ]
    assert _filter_actors(payload, exclude_login="") == payload


def test_filter_actors_skips_non_dict_entries() -> None:
    payload: list[Any] = [
        "not a dict",
        None,
        {"user": {"login": "a"}, "body": "ok"},
    ]
    out = _filter_actors(payload, exclude_login="")
    assert len(out) == 1
    assert out[0]["body"] == "ok"


def test_filter_actors_tolerates_missing_user() -> None:
    """A comment without `user` key is kept — we can't match it against
    the exclude list, but it's still part of the PR conversation."""
    payload: list[Any] = [
        {"body": "no user attached"},
        {"user": "string-not-dict", "body": "weird"},
    ]
    out = _filter_actors(payload, exclude_login="rebel-hyejin")
    # Both kept — neither matches the exclude login.
    assert len(out) == 2


def test_filter_reviews_drops_pending() -> None:
    payload: list[Any] = [
        {"user": {"login": "a"}, "state": "COMMENTED", "submitted_at": "2026-06-22T00:00:00Z"},
        {"user": {"login": "b"}, "state": "PENDING", "submitted_at": None},
        {"user": {"login": "c"}, "state": "COMMENTED"},  # no submitted_at
    ]
    out = _filter_reviews(payload, exclude_login="")
    assert len(out) == 1
    assert out[0]["user"]["login"] == "a"


def test_filter_reviews_drops_excluded_and_pending() -> None:
    payload: list[Any] = [
        {
            "user": {"login": "rebel-hyejin"},
            "state": "COMMENTED",
            "submitted_at": "2026-06-22T00:00:00Z",
        },
        {
            "user": {"login": "rebel-daeyeonlee"},
            "state": "COMMENTED",
            "submitted_at": "2026-06-22T00:00:00Z",
        },
    ]
    out = _filter_reviews(payload, exclude_login="rebel-hyejin")
    assert len(out) == 1
    assert out[0]["user"]["login"] == "rebel-daeyeonlee"
