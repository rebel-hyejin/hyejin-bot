"""structlog wiring + secret-redaction processor.

Redaction is mandatory before any handler logs Claude payloads. We match
known token shapes (Slack, AWS, JWT, Anthropic OAuth, GitHub) plus a
high-entropy fallback so unknown-shape secrets don't slip through.

The redactor walks the structlog event_dict recursively (dicts, lists,
strings) and replaces matches with `***REDACTED***`.
"""

from __future__ import annotations

import logging
import math
import re
import sys
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any, cast

import structlog

REDACTED = "***REDACTED***"

# Order matters only for performance; matches are replaced wherever they appear.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS Access Key ID
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
    re.compile(r"sk-ant-oat[A-Za-z0-9_-]{10,}"),  # Anthropic OAuth
    re.compile(r"sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{10,}"),  # Anthropic API key
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),  # GitHub personal access token
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub fine-grained PAT
)

# Entropy fallback parameters.
_MIN_ENTROPY_BITS_PER_CHAR = 4.5
_MIN_TOKEN_LEN = 24
_TOKENISH_RE = re.compile(r"[A-Za-z0-9_-]{24,}")


def init(*, level: str = "INFO", fmt: str = "console") -> None:
    """Initialize structlog with a redaction processor."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stderr, level=log_level, format="%(message)s")

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_processor,
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def redact_processor(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: returns a new event_dict with secrets redacted."""
    return cast("MutableMapping[str, Any]", _redact_value(event_dict))


def redact_text(text: str) -> str:
    """Redact known-shape and high-entropy tokens in `text`."""
    out = text
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(REDACTED, out)
    out = _TOKENISH_RE.sub(_maybe_redact_match, out)
    return out


def _maybe_redact_match(match: re.Match[str]) -> str:
    candidate = match.group(0)
    if _shannon_entropy(candidate) >= _MIN_ENTROPY_BITS_PER_CHAR:
        return REDACTED
    return candidate


def _shannon_entropy(s: str) -> float:
    """Bits/character via Shannon entropy. Empty / single-char strings → 0.0."""
    if len(s) < _MIN_TOKEN_LEN:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {key: _redact_value(v) for key, v in mapping.items()}
    if isinstance(value, tuple):
        items = cast("tuple[object, ...]", value)
        return tuple(_redact_value(v) for v in items)
    if isinstance(value, list):
        items_list = cast("list[object]", value)
        return [_redact_value(v) for v in items_list]
    if isinstance(value, Iterable) and not isinstance(value, bytes | bytearray):
        iterable = cast("Iterable[object]", value)
        return [_redact_value(v) for v in iterable]  # pragma: no cover — defensive
    return value


__all__ = ["REDACTED", "init", "redact_processor", "redact_text"]
