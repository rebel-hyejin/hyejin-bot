"""Configuration loader (pydantic-settings, TOML + .env).

Phase 0: minimal model so `daeyeon-bot doctor` can report missing fields.
The full schema lands in Phase 1. We accept extra keys for forward compat
since real triggers/handlers add their own subsections.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSection(BaseModel):
    state_dir: str = "~/.daeyeon-bot"
    shutdown_budget_seconds: int = 180


class LoggingSection(BaseModel):
    level: str = "INFO"
    format: str = "json"


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DAEYEON_BOT__",
        env_nested_delimiter="__",
        extra="allow",
    )

    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    raw: dict[str, Any] = Field(default_factory=dict)


def _resolve_config_path(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("DAEYEON_BOT_CONFIG")
    if env:
        return Path(env).expanduser()
    default = Path.cwd() / "config.toml"
    return default if default.exists() else None


def load(path: str | None = None) -> Config:
    """Load config from TOML (if present) and environment overrides."""
    config_path = _resolve_config_path(path)
    if config_path and config_path.is_file():
        with config_path.open("rb") as fp:
            data = tomllib.load(fp)
        return Config(**data, raw=data)
    return Config()
