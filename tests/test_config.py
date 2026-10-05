"""Smoke-тесты конфиг-loader'а."""

from __future__ import annotations

import pytest

from src.core import config as config_module
from src.core.config import MissingConfigError, Settings, require


def test_settings_loads_from_env(monkeypatch):
    for key, value in {
        "SUPABASE_DB_URL": "postgresql://x",
        "R2_ACCESS_KEY_ID": "k",
        "R2_SECRET_ACCESS_KEY": "s",
        "R2_ACCOUNT_ID": "a",
        "R2_BUCKET_FOOTAGE": "f",
        "R2_BUCKET_RENDERS": "r",
        "ANTHROPIC_API_KEY": "ant",
        "TELEGRAM_BOT_TOKEN": "tg",
        "TELEGRAM_OWNER_CHAT_ID": "1",
    }.items():
        monkeypatch.setenv(key, value)
    s = Settings()
    assert s.SUPABASE_DB_URL == "postgresql://x"
    require("epic1", s)


def test_missing_key_raises(monkeypatch):
    s = Settings(
        SUPABASE_DB_URL="postgresql://x",
        R2_ACCESS_KEY_ID="",
        R2_SECRET_ACCESS_KEY="s",
        R2_ACCOUNT_ID="a",
        R2_BUCKET_FOOTAGE="f",
        R2_BUCKET_RENDERS="r",
        ANTHROPIC_API_KEY="ant",
        TELEGRAM_BOT_TOKEN="tg",
        TELEGRAM_OWNER_CHAT_ID="1",
    )
    with pytest.raises(MissingConfigError) as ei:
        require("epic1", s)
    assert "R2_ACCESS_KEY_ID" in ei.value.missing


def test_project_root_exists():
    assert config_module.project_root().is_dir()
