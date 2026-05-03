"""Configuration loader (pydantic-settings, TOML + .env).

The config object is the *whole* configuration surface, validated once at boot.
Trigger / handler / routing sections are dictionaries keyed by name.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSection(BaseModel):
    state_dir: str = "~/.daeyeon-bot"
    shutdown_budget_seconds: int = 180


class LoggingSection(BaseModel):
    level: str = "INFO"
    format: str = "json"


class RetentionSection(BaseModel):
    events_days: int = 90
    runs_days: int = 30
    runs_keep_per_handler: int = 10
    dedup_default_ttl_days: int = 7
    backup_keep: int = 5


class RateLimitDefaults(BaseModel):
    global_per_hour: int = 30
    global_per_day: int = 200
    handler_per_hour: int = 10


class RateLimitSection(BaseModel):
    defaults: RateLimitDefaults = Field(default_factory=RateLimitDefaults)


class SecretsSection(BaseModel):
    provider: str = "keychain"
    keychain_service: str = "daeyeon-bot"
    keychain_account: str = "oauth_token"
    file_path: str = "/etc/daeyeon-bot/oauth_token"


class ClaudeSection(BaseModel):
    model: str = "claude-opus-4-7"
    default_system_prompt: str = "You are daeyeon's helpful assistant."


class TriggerEntry(BaseModel):
    """Runtime override for a trigger. Extra keys are passed to the trigger constructor."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = True


class HandlerEntry(BaseModel):
    """Runtime override for a handler. Mirrors HandlerManifest fields."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    idempotent: bool | None = None
    dedup_ttl_seconds: int | None = None
    side_effect_key: str | None = None
    concurrency: int | None = None
    accepts: list[str] | None = None


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DAEYEON_BOT__",
        env_nested_delimiter="__",
        extra="allow",
    )

    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    retention: RetentionSection = Field(default_factory=RetentionSection)
    ratelimit: RateLimitSection = Field(default_factory=RateLimitSection)
    secrets: SecretsSection = Field(default_factory=SecretsSection)
    claude: ClaudeSection = Field(default_factory=ClaudeSection)

    triggers: dict[str, TriggerEntry] = Field(default_factory=dict)
    handlers: dict[str, HandlerEntry] = Field(default_factory=dict)
    routing: dict[str, list[str]] = Field(default_factory=dict)

    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def state_dir_path(self) -> Path:
        return Path(self.runtime.state_dir).expanduser()

    @property
    def db_path(self) -> Path:
        return self.state_dir_path / "state.db"

    @property
    def pause_flag_path(self) -> Path:
        return self.state_dir_path / "PAUSE"

    @property
    def pidfile_path(self) -> Path:
        return self.state_dir_path / "daeyeon-bot.pid"


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
