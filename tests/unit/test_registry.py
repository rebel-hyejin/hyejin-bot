"""Handler registry: instantiation, manifest overrides, routing lookup."""

from __future__ import annotations

import pytest

from daeyeon_bot.app.config import Config, HandlerEntry
from daeyeon_bot.app.registry import build_handler_registry
from daeyeon_bot.core.errors import ConfigError


def test_echo_registered_with_default_manifest() -> None:
    cfg = Config(
        handlers={"echo": HandlerEntry()},
        routing={"manual.message": ["echo"]},
    )
    registry = build_handler_registry(cfg)
    record = registry.by_name["echo"]
    assert record.manifest.idempotent is True
    assert record.manifest.dedup_ttl.days == 1
    assert registry.handlers_for("manual.message") == [record]
    assert registry.handlers_for("unknown") == []


def test_handler_disabled_means_unregistered() -> None:
    cfg = Config(
        handlers={"echo": HandlerEntry(enabled=False)},
        routing={"manual.message": ["echo"]},
    )
    registry = build_handler_registry(cfg)
    assert "echo" not in registry.by_name


def test_manifest_overrides_apply() -> None:
    cfg = Config(
        handlers={
            "echo": HandlerEntry(
                concurrency=4,
                dedup_ttl_seconds=3600,
                accepts=["custom.type"],
            )
        },
        routing={"custom.type": ["echo"]},
    )
    registry = build_handler_registry(cfg)
    manifest = registry.by_name["echo"].manifest
    assert manifest.concurrency == 4
    assert manifest.dedup_ttl.total_seconds() == 3600
    assert manifest.accepts == ("custom.type",)


def test_unknown_handler_name_raises() -> None:
    cfg = Config(handlers={"nonexistent": HandlerEntry()})
    with pytest.raises(ConfigError):
        build_handler_registry(cfg)
