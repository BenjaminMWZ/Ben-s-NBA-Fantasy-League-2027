"""Settings loaded from the project-root .env file.

Secrets are read from .env only (via pydantic-settings / python-dotenv); values in
.env take priority over process environment variables so a stray global export
does not silently change what the bot uses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"


class ConfigError(RuntimeError):
    """Raised when a feature needs a setting that is missing from .env."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    llm_provider: Literal["gemini", "claude"] = "gemini"
    llm_model: str = ""
    # Gemini only: used when the main model is overloaded / times out
    llm_fallback_model: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    # Claude only: low | medium | high | xhigh | max (empty = model default)
    llm_effort: str = ""
    enable_web_search: bool = False

    # ESPN fantasy
    espn_league_id: int | None = None
    espn_season_year: int = 2027
    espn_s2: str = ""
    espn_swid: str = ""
    espn_my_team_name: str = ""

    # Misc
    timezone: str = "America/Los_Angeles"
    log_level: str = "INFO"

    @field_validator("espn_league_id", mode="before")
    @classmethod
    def _empty_league_id(cls, v):
        return None if v in ("", None) else v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # .env wins over the process environment.
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    # ---- feature-level checks -------------------------------------------------

    def missing_for_llm(self) -> list[str]:
        missing = []
        if not self.llm_model:
            missing.append("LLM_MODEL")
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if self.llm_provider == "claude" and not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing

    def missing_for_fantasy(self) -> list[str]:
        missing = []
        if self.espn_league_id is None:
            missing.append("ESPN_LEAGUE_ID")
        if bool(self.espn_s2) != bool(self.espn_swid):
            missing.append("ESPN_S2 和 ESPN_SWID 需要同时填写")
        return missing

    def require_llm(self) -> None:
        _raise_if_missing("LLM", self.missing_for_llm())

    def require_fantasy(self) -> None:
        _raise_if_missing("ESPN Fantasy", self.missing_for_fantasy())


def _raise_if_missing(feature: str, missing: list[str]) -> None:
    if missing:
        hint = "" if ENV_FILE.exists() else "（还没有 .env，先 `cp .env.example .env`）"
        raise ConfigError(f"{feature} 配置不完整，请在 .env 中填写: {', '.join(missing)}{hint}")


def _load() -> Settings:
    try:
        return Settings()
    except Exception as e:  # pydantic ValidationError -> readable message
        raise ConfigError(f".env 配置有误: {e}") from None


settings = _load()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
