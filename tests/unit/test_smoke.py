"""Phase 0 smoke tests — package imports cleanly and contracts are visible."""

from __future__ import annotations

from datetime import timedelta


def test_package_imports() -> None:
    """`hyejin_bot` and its main subpackages import without side effects."""
    import hyejin_bot
    import hyejin_bot.app
    import hyejin_bot.cli
    import hyejin_bot.core
    import hyejin_bot.handlers
    import hyejin_bot.infra
    import hyejin_bot.triggers

    assert isinstance(hyejin_bot.__version__, str)
    # Reference packages so import-only side effects are exercised.
    for pkg in (
        hyejin_bot.app,
        hyejin_bot.cli,
        hyejin_bot.core,
        hyejin_bot.handlers,
        hyejin_bot.infra,
        hyejin_bot.triggers,
    ):
        assert pkg.__name__.startswith("hyejin_bot.")


def test_results_are_distinct_types() -> None:
    """Ack/Retry/DeadLetter are nominal types so pattern matching can switch on them."""
    from hyejin_bot.core.results import Ack, DeadLetter, Retry

    assert Ack() == Ack()
    assert Retry(after_s=1.0) != Retry(after_s=2.0)
    assert DeadLetter(reason="x").reason == "x"


def test_handler_manifest_freezes() -> None:
    """Manifests are frozen dataclasses — accidental mutation is a TypeError."""
    import dataclasses

    from hyejin_bot.core.manifest import HandlerManifest

    m = HandlerManifest(
        name="echo",
        idempotent=True,
        dedup_ttl=timedelta(days=1),
        side_effect_key=None,
        concurrency=1,
        accepts=("manual.message",),
    )

    try:
        m.name = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("HandlerManifest must be frozen")


def test_echo_handler_manifest_visible() -> None:
    """The echo handler exposes its MANIFEST as the wiring layer expects."""
    from hyejin_bot.handlers import echo

    assert echo.MANIFEST.name == "echo"
    assert "manual.message" in echo.MANIFEST.accepts
