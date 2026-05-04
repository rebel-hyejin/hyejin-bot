"""Helper-function tests for `handlers/pr_review.py`.

Covers the small pure helpers that the main handler tests don't exercise
directly: payload parsing, code-fence stripping, snapshot reads, summary
folding edge cases, inline-to-API conversion, render-user-message branches.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daeyeon_bot.core.errors import ValidationError
from daeyeon_bot.core.events import Event, make_event
from daeyeon_bot.handlers.pr_review import (
    _append_folded_bullets,  # pyright: ignore[reportPrivateUsage]
    _inline_to_api,  # pyright: ignore[reportPrivateUsage]
    _parse_payload,  # pyright: ignore[reportPrivateUsage]
    _read_author,  # pyright: ignore[reportPrivateUsage]
    _read_head_sha,  # pyright: ignore[reportPrivateUsage]
    _read_requested_logins,  # pyright: ignore[reportPrivateUsage]
    _read_review_id,  # pyright: ignore[reportPrivateUsage]
    _read_submitted_at,  # pyright: ignore[reportPrivateUsage]
    _render_user_message,  # pyright: ignore[reportPrivateUsage]
    _strip_code_fence,  # pyright: ignore[reportPrivateUsage]
)
from daeyeon_bot.handlers.pr_review_schemas import InlineComment

# ── _parse_payload ─────────────────────────────────────────────────────────


def _event(payload: dict[str, object]) -> Event:
    return make_event(
        type="pr.review.manual",
        payload=payload,
        created_at=datetime(2026, 5, 4, tzinfo=UTC),
    )


def test_parse_payload_returns_normalized_tuple() -> None:
    ev = _event(
        {
            "repo": "octo/cat",
            "pr_number": 7,
            "head_sha": "abc",
            "request_gen": "1",
            "force": True,
        }
    )
    repo, pr_number, head_sha, gen, force = _parse_payload(ev)
    assert (repo, pr_number, head_sha, gen, force) == ("octo/cat", 7, "abc", "1", True)


def test_parse_payload_defaults_request_gen_to_zero() -> None:
    ev = _event({"repo": "o/r", "pr_number": 1, "head_sha": "z"})
    _, _, _, gen, force = _parse_payload(ev)
    assert gen == "0"
    assert force is False


def test_parse_payload_missing_repo_raises() -> None:
    ev = _event({"pr_number": 1, "head_sha": "z"})
    with pytest.raises(ValidationError):
        _parse_payload(ev)


def test_parse_payload_missing_pr_number_raises() -> None:
    ev = _event({"repo": "o/r", "head_sha": "z"})
    with pytest.raises(ValidationError):
        _parse_payload(ev)


def test_parse_payload_missing_head_sha_raises() -> None:
    ev = _event({"repo": "o/r", "pr_number": 1})
    with pytest.raises(ValidationError):
        _parse_payload(ev)


# ── _read_head_sha / _read_author / _read_requested_logins ────────────────


def test_read_head_sha_handles_missing_or_malformed() -> None:
    assert _read_head_sha({"head": {"sha": "abc"}}) == "abc"
    assert _read_head_sha({"head": {}}) is None
    assert _read_head_sha({"head": {"sha": 42}}) is None
    assert _read_head_sha({"head": "not a dict"}) is None
    assert _read_head_sha({}) is None


def test_read_author_handles_malformed_user() -> None:
    assert _read_author({"user": {"login": "alice"}}) == "alice"
    assert _read_author({"user": {}}) == ""
    assert _read_author({"user": {"login": 1}}) == ""
    assert _read_author({"user": "not a dict"}) == ""
    assert _read_author({}) == ""


def test_read_requested_logins_filters_non_dicts_and_non_strs() -> None:
    pr = {
        "requested_reviewers": [
            {"login": "alice"},
            "not a dict",
            {"login": 42},
            {"no_login_key": True},
            {"login": "bob"},
        ]
    }
    assert _read_requested_logins(pr) == ("alice", "bob")


def test_read_requested_logins_with_non_list() -> None:
    assert _read_requested_logins({"requested_reviewers": "not a list"}) == ()
    assert _read_requested_logins({}) == ()


# ── _read_review_id / _read_submitted_at ──────────────────────────────────


def test_read_review_id_accepts_int_and_digit_str() -> None:
    assert _read_review_id({"id": 123}) == 123
    assert _read_review_id({"id": "456"}) == 456
    assert _read_review_id({"id": "not-a-number"}) is None
    assert _read_review_id({"id": None}) is None
    assert _read_review_id({}) is None


def test_read_submitted_at_parses_z_suffixed_iso() -> None:
    ts = _read_submitted_at({"submitted_at": "2026-05-04T12:34:56Z"})
    assert ts is not None
    assert ts.year == 2026 and ts.month == 5 and ts.day == 4


def test_read_submitted_at_handles_invalid_inputs() -> None:
    assert _read_submitted_at({}) is None
    assert _read_submitted_at({"submitted_at": ""}) is None
    assert _read_submitted_at({"submitted_at": 42}) is None
    assert _read_submitted_at({"submitted_at": "not-a-date"}) is None


# ── _strip_code_fence ─────────────────────────────────────────────────────


def test_strip_code_fence_unfenced_passthrough() -> None:
    assert _strip_code_fence('{"x": 1}') == '{"x": 1}'


def test_strip_code_fence_with_lang_tag_and_trailing_fence() -> None:
    src = '```json\n{"x": 1}\n```'
    assert _strip_code_fence(src) == '{"x": 1}'


def test_strip_code_fence_with_only_opening_fence() -> None:
    src = '```\n{"x": 1}\n'
    assert _strip_code_fence(src) == '{"x": 1}'


def test_strip_code_fence_strips_extra_whitespace() -> None:
    src = '   ```\n{"x": 1}\n```   '
    assert _strip_code_fence(src) == '{"x": 1}'


# ── _append_folded_bullets ────────────────────────────────────────────────


def test_append_folded_bullets_no_op_when_empty() -> None:
    assert _append_folded_bullets("Summary text.", []) == "Summary text."


def test_append_folded_bullets_handles_trailing_newline() -> None:
    out = _append_folded_bullets(
        "Summary line.\n",
        [InlineComment(path="a.py", line=3, side="RIGHT", body="nit")],
    )
    assert out.startswith("Summary line.\n\n- [a.py near L3]")


def test_append_folded_bullets_no_trailing_newline() -> None:
    out = _append_folded_bullets(
        "Summary line.",
        [InlineComment(path="a.py", line=3, side="RIGHT", body="nit")],
    )
    assert out.startswith("Summary line.\n\n- [a.py near L3]")


# ── _inline_to_api ────────────────────────────────────────────────────────


def test_inline_to_api_single_line_anchor() -> None:
    payload = _inline_to_api(InlineComment(path="x.py", line=5, side="RIGHT", body="ok"))
    assert payload == {"path": "x.py", "line": 5, "side": "RIGHT", "body": "ok"}


def test_inline_to_api_multi_line_anchor_includes_start_line() -> None:
    payload = _inline_to_api(
        InlineComment(path="x.py", line=10, side="RIGHT", body="ok", start_line=5)
    )
    assert payload["start_line"] == 5
    assert payload["start_side"] == "RIGHT"


# ── _render_user_message ──────────────────────────────────────────────────


def test_render_user_message_omits_diff_for_non_string_patch() -> None:
    files = [
        {
            "filename": "a.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": None,
        }
    ]
    out = _render_user_message(
        repo="o/r",
        pr_number=1,
        title="t",
        body="b",
        author_login="alice",
        head_sha="abc",
        files=files,
    )
    assert "(binary or oversized — diff omitted)" in out
    assert "```diff" not in out


def test_render_user_message_includes_diff_when_patch_is_string() -> None:
    files = [
        {
            "filename": "a.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "@@ -1,1 +1,1 @@\n-old\n+new\n",
        }
    ]
    out = _render_user_message(
        repo="o/r",
        pr_number=1,
        title="t",
        body="b",
        author_login="alice",
        head_sha="abc",
        files=files,
    )
    assert "```diff" in out
    assert "@@ -1,1 +1,1 @@" in out
