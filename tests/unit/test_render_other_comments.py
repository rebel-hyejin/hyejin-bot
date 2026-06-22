"""`render_user_message` cross-actor comments section.

Drives the `other_comments` branch added for SKILL.md trait #9 dedup.
"""

from __future__ import annotations

from hyejin_bot.handlers.pr_review_render import render_user_message


def _base_args(*, other_comments: dict[str, list[dict[str, object]]] | None = None) -> str:
    return render_user_message(
        repo="o/r",
        pr_number=7,
        title="t",
        body="b",
        author_login="alice",
        head_sha="deadbeef",
        files=[],
        other_comments=other_comments,
    )


def test_empty_other_comments_omits_section() -> None:
    """All three buckets empty → no `Existing PR comments by other actors` header."""
    out = _base_args(other_comments={
        "review_comments": [],
        "issue_comments": [],
        "pull_request_reviews": [],
    })
    assert "Existing PR comments by other actors" not in out


def test_none_other_comments_omits_section() -> None:
    out = _base_args(other_comments=None)
    assert "Existing PR comments by other actors" not in out


def test_review_comments_rendered_with_file_line_anchor() -> None:
    other: dict[str, list[dict[str, object]]] = {
        "review_comments": [
            {
                "user": {"login": "rebel-daeyeonlee"},
                "path": "src/foo.py",
                "line": 42,
                "body": "guard repetition again",
                "html_url": "https://github.com/o/r/pull/7#discussion_r1",
            }
        ],
        "issue_comments": [],
        "pull_request_reviews": [],
    }
    out = _base_args(other_comments=other)
    assert "Existing PR comments by other actors" in out
    assert "review_comments: 1" in out
    assert "@rebel-daeyeonlee on src/foo.py:42 — guard repetition again" in out
    assert "<https://github.com/o/r/pull/7#discussion_r1>" in out


def test_issue_comments_rendered_without_file_line() -> None:
    other: dict[str, list[dict[str, object]]] = {
        "review_comments": [],
        "issue_comments": [
            {
                "user": {"login": "human"},
                "body": "let's hold on this",
                "html_url": "https://github.com/o/r/pull/7#issuecomment-9",
            }
        ],
        "pull_request_reviews": [],
    }
    out = _base_args(other_comments=other)
    assert "issue_comments: 1" in out
    assert "@human — let's hold on this" in out


def test_pull_request_reviews_rendered_with_state() -> None:
    other: dict[str, list[dict[str, object]]] = {
        "review_comments": [],
        "issue_comments": [],
        "pull_request_reviews": [
            {
                "user": {"login": "Copilot"},
                "state": "COMMENTED",
                "body": "consider error handling",
                "html_url": "https://github.com/o/r/pull/7#pullrequestreview-5",
            }
        ],
    }
    out = _base_args(other_comments=other)
    assert "pull_request_reviews: 1" in out
    assert "@Copilot (COMMENTED) — consider error handling" in out


def test_long_body_is_truncated() -> None:
    long_body = "x" * 500
    other: dict[str, list[dict[str, object]]] = {
        "review_comments": [
            {
                "user": {"login": "x"},
                "path": "f",
                "line": 1,
                "body": long_body,
                "html_url": "u",
            }
        ],
        "issue_comments": [],
        "pull_request_reviews": [],
    }
    out = _base_args(other_comments=other)
    assert "..." in out
    # Cap is 240 — no 500-char span should survive.
    assert long_body not in out
