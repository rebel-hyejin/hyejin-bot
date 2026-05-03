"""Phase 0 config: defaults are sane; example file parses."""

from __future__ import annotations

from pathlib import Path

from daeyeon_bot.app.config import Config, load


def test_defaults_when_no_config() -> None:
    cfg = Config()
    assert cfg.runtime.shutdown_budget_seconds == 180
    assert cfg.logging.level == "INFO"
    assert cfg.logging.format == "json"


def test_example_toml_parses(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[2] / "config.example.toml"
    dst = tmp_path / "config.toml"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    cfg = load(str(dst))
    assert cfg.runtime.state_dir.endswith("daeyeon-bot")
    assert cfg.logging.format in {"json", "console"}
    # routing / triggers / handlers go into `raw` for now (Phase 1 promotes them to typed).
    assert "routing" in cfg.raw or "triggers" in cfg.raw
