"""structlog wiring with redaction. Phase 0: minimal init so CLI prints something sane."""

from __future__ import annotations

import logging
import sys

import structlog


def init(*, level: str = "INFO", fmt: str = "console") -> None:
    """Initialize structlog. Phase 4 will replace this with redaction processors."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stderr, level=log_level, format="%(message)s")

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
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
