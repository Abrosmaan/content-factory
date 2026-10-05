"""Конфиг-loader. Грузит .env, валидирует обязательные ключи по этапам.

Архитектурная константа 3 ТЗ: voice ID, owner chat ID и api_audit_passed
читаются ТОЛЬКО отсюда (ENV или settings таблица). Подмена в коде запрещена.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(_ENV_PATH, override=False)

Stage = Literal[
    "epic1", "epic2", "epic3", "epic4", "epic5",
    "epic6", "epic6_pinterest", "epic6_threads",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Epic 1: инфраструктура ---
    SSH_HOST: str = ""
    SSH_USER: str = ""
    SSH_PRIVATE_KEY_PATH: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_DB_URL: str = ""

    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_ACCOUNT_ID: str = ""
    R2_BUCKET_FOOTAGE: str = ""
    R2_BUCKET_RENDERS: str = ""

    ANTHROPIC_API_KEY: str = ""

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_OWNER_CHAT_ID: str = ""

    # --- Epic 2: производственное ядро ---
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""
    FAL_API_KEY: str = ""
    CREATOMATE_API_KEY: str = ""
    CREATOMATE_TEMPLATE_SHORT: str = ""  # готовый шаблон для short-формата

    # --- Epic 3: публикация ---
    YT_CLIENT_ID: str = ""
    YT_CLIENT_SECRET: str = ""
    YT_REFRESH_TOKEN: str = ""

    # --- Epic 4: источники ---
    # AL-002: Reddit перешёл с API на RSS — auth не требуется.

    # --- Epic 5: мониторинг ---
    HEALTHCHECKS_PING_URL: str = ""

    # --- Epic 6: дистрибуция ---
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = ""
    BLOG_DOMAIN: str = ""

    PINTEREST_ACCESS_TOKEN: str = ""
    PINTEREST_BOARD_ID_GAMING: str = ""
    PINTEREST_BOARD_ID_SETUP: str = ""

    THREADS_USER_ID: str = ""
    THREADS_ACCESS_TOKEN: str = ""

    # --- Внутренние ---
    LOG_LEVEL: str = Field(default="INFO")
    ENV: str = Field(default="dev")


_STAGE_REQUIRED: dict[Stage, tuple[str, ...]] = {
    "epic1": (
        "SUPABASE_DB_URL",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ACCOUNT_ID",
        "R2_BUCKET_FOOTAGE",
        "R2_BUCKET_RENDERS",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_CHAT_ID",
    ),
    "epic2": (
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "FAL_API_KEY",
        "CREATOMATE_API_KEY",
    ),
    "epic3": ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"),
    "epic4": (),  # AL-002: Reddit на RSS, нет required ключей
    "epic5": ("HEALTHCHECKS_PING_URL",),
    "epic6": (
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "BLOG_DOMAIN",
    ),
    "epic6_pinterest": (
        "PINTEREST_ACCESS_TOKEN",
        "PINTEREST_BOARD_ID_GAMING",
        "PINTEREST_BOARD_ID_SETUP",
    ),
    "epic6_threads": (
        "THREADS_USER_ID",
        "THREADS_ACCESS_TOKEN",
    ),
}


class MissingConfigError(RuntimeError):
    def __init__(self, stage: Stage, missing: list[str]) -> None:
        super().__init__(
            f"Missing required ENV for {stage}: {', '.join(missing)}. "
            f"Заполни .env и перезапусти."
        )
        self.stage = stage
        self.missing = missing


def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        raise MissingConfigError("epic1", [str(err) for err in e.errors()]) from e


def require(stage: Stage, settings: Settings | None = None) -> Settings:
    """Проверка обязательных ключей перед запуском зависимого кода."""
    s = settings or get_settings()
    missing = [name for name in _STAGE_REQUIRED[stage] if not getattr(s, name, "")]
    if missing:
        raise MissingConfigError(stage, missing)
    return s


def project_root() -> Path:
    return PROJECT_ROOT


__all__ = [
    "Settings",
    "Stage",
    "MissingConfigError",
    "get_settings",
    "require",
    "project_root",
]


if __name__ == "__main__":
    import sys

    try:
        s = require("epic1")
    except MissingConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"[config] epic1 ENV OK (env={s.ENV})")
