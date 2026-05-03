"""Phase 4 redaction processor: known patterns + entropy fallback."""

from __future__ import annotations

import secrets as stdlib_secrets
from typing import cast

from daeyeon_bot.infra.logging import REDACTED, redact_processor, redact_text


def test_slack_token_redacted() -> None:
    text = "before xoxb-1234567890-abcdefghij after"
    assert REDACTED in redact_text(text)
    assert "xoxb-" not in redact_text(text)


def test_aws_access_key_redacted() -> None:
    text = "AKIAIOSFODNN7EXAMPLE shown"
    assert REDACTED in redact_text(text)


def test_jwt_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature_part_here"
    assert redact_text(jwt) == REDACTED


def test_anthropic_oauth_token_redacted() -> None:
    token = "sk-ant-oat" + "A" * 30
    assert redact_text(f"got {token}") == f"got {REDACTED}"


def test_anthropic_api_key_redacted() -> None:
    key = "sk-ant-api01-" + "A" * 30
    assert redact_text(key) == REDACTED


def test_github_pat_redacted() -> None:
    pat = "ghp_" + "A" * 36
    assert redact_text(pat) == REDACTED


def test_github_fine_grained_pat_redacted() -> None:
    pat = "github_pat_" + "A" * 40
    assert redact_text(pat) == REDACTED


def test_high_entropy_random_token_redacted() -> None:
    high_entropy = stdlib_secrets.token_urlsafe(48)
    out = redact_text(high_entropy)
    assert out == REDACTED


def test_low_entropy_string_preserved() -> None:
    low_entropy = "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # 28 a's
    assert redact_text(low_entropy) == low_entropy


def test_short_string_preserved_even_if_high_entropy() -> None:
    short = "Az9-_xY"
    assert redact_text(short) == short


def test_processor_redacts_nested_dict() -> None:
    event = {
        "msg": "boot",
        "headers": {"authorization": "Bearer " + stdlib_secrets.token_urlsafe(48)},
    }
    out = redact_processor(None, "info", event)
    headers = out["headers"]
    assert isinstance(headers, dict)
    assert REDACTED in headers["authorization"]


def test_processor_redacts_list() -> None:
    event: dict[str, object] = {
        "msg": "ok",
        "tokens": ["xoxb-12345-abcdefghij", "ghp_" + "B" * 36],
    }
    out = redact_processor(None, "info", event)
    tokens = cast("list[str]", out["tokens"])
    assert all(REDACTED in t for t in tokens)


def test_processor_preserves_non_secret_strings() -> None:
    event = {"msg": "user said hi", "level": "info"}
    out = redact_processor(None, "info", event)
    assert out["msg"] == "user said hi"
    assert out["level"] == "info"


def test_processor_preserves_non_string_scalars() -> None:
    event = {"count": 7, "ratio": 0.5, "flag": True, "nil": None}
    out = redact_processor(None, "info", event)
    assert out["count"] == 7
    assert out["ratio"] == 0.5
    assert out["flag"] is True
    assert out["nil"] is None


def test_processor_handles_tuple_values() -> None:
    event = {"items": ("xoxb-12345-abcdefghij", "plain")}
    out = redact_processor(None, "info", event)
    items = out["items"]
    assert isinstance(items, tuple)
    assert REDACTED in items[0]
    assert items[1] == "plain"


def test_redact_text_handles_multiple_secrets_in_one_string() -> None:
    text = "auth=xoxb-1234567890-abcdefghij and key=AKIAIOSFODNN7EXAMPLE done"
    out = redact_text(text)
    assert "xoxb-" not in out
    assert "AKIA" not in out
    assert out.count(REDACTED) >= 2
