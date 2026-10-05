"""Kill switches: настройки в settings_store, гейты в воркерах,
/switches UI-хелперы (без aiogram)."""

from __future__ import annotations

from unittest.mock import patch

from src.core import settings_store


# ---------- settings_store helpers ----------

def test_switch_keys_include_all():
    keys = {k for k, _ in settings_store.SWITCH_KEYS}
    assert settings_store.PAUSE_KEY in keys
    assert settings_store.YOUTUBE_UPLOAD_PAUSED_KEY in keys
    assert settings_store.DISTRIBUTION_PAUSED_KEY in keys
    assert settings_store.TEXT_NEWS_PAUSED_KEY in keys


def test_is_youtube_upload_paused_defaults_false(monkeypatch):
    monkeypatch.setattr(
        settings_store, "get_flag", lambda k, default=False: default,
    )
    assert settings_store.is_youtube_upload_paused() is False


def test_is_distribution_paused_reads_flag(monkeypatch):
    stored = {settings_store.DISTRIBUTION_PAUSED_KEY: True}
    monkeypatch.setattr(
        settings_store, "get_flag",
        lambda k, default=False: stored.get(k, default),
    )
    assert settings_store.is_distribution_paused() is True
    assert settings_store.is_text_news_paused() is False


def test_toggle_switch_flips_and_returns_new_state(monkeypatch):
    state = {"pipeline_paused": False}
    monkeypatch.setattr(
        settings_store, "get_flag",
        lambda k, default=False: state.get(k, default),
    )
    def _set(k, v):
        state[k] = v
    monkeypatch.setattr(settings_store, "set_flag", _set)

    new = settings_store.toggle_switch("pipeline_paused")
    assert new is True
    assert state["pipeline_paused"] is True

    new = settings_store.toggle_switch("pipeline_paused")
    assert new is False
    assert state["pipeline_paused"] is False


# ---------- воркер-гейты ----------

def test_publisher_skips_when_youtube_paused(monkeypatch):
    from src.workers import publisher
    monkeypatch.setattr(
        settings_store, "is_youtube_upload_paused", lambda: True,
    )
    with patch.object(publisher, "_claim") as claim:
        result = publisher.run_once()
    assert result == 0
    claim.assert_not_called()


def test_publisher_proceeds_when_not_paused(monkeypatch):
    from src.workers import publisher
    monkeypatch.setattr(
        settings_store, "is_youtube_upload_paused", lambda: False,
    )
    with patch.object(publisher, "_claim", return_value=None):
        assert publisher.run_once() == 0


def test_blog_publisher_skips_when_distribution_paused(monkeypatch):
    from src.workers import blog_publisher
    monkeypatch.setattr(
        settings_store, "is_distribution_paused", lambda: True,
    )
    with patch.object(blog_publisher, "_claim_content_ready") as claim:
        assert blog_publisher.run_once() == 0
    claim.assert_not_called()


def test_pinterest_publisher_skips_when_distribution_paused(monkeypatch):
    from src.workers import pinterest_publisher
    monkeypatch.setattr(
        settings_store, "is_distribution_paused", lambda: True,
    )
    with patch.object(pinterest_publisher, "_claim_for_publish") as claim:
        assert pinterest_publisher.run_once() == 0
    claim.assert_not_called()


def test_threads_publisher_skips_when_distribution_paused(monkeypatch):
    from src.workers import threads_publisher
    monkeypatch.setattr(
        settings_store, "is_distribution_paused", lambda: True,
    )
    with patch.object(threads_publisher, "_claim_for_publish") as claim:
        assert threads_publisher.run_once() == 0
    claim.assert_not_called()


# ---------- /switches UI helpers ----------

def test_format_switches_text_shows_states(monkeypatch):
    from src.bot import handlers
    state = {
        settings_store.PAUSE_KEY: False,
        settings_store.YOUTUBE_UPLOAD_PAUSED_KEY: True,
        settings_store.DISTRIBUTION_PAUSED_KEY: False,
        settings_store.TEXT_NEWS_PAUSED_KEY: False,
    }
    monkeypatch.setattr(
        settings_store, "get_flag",
        lambda k, default=False: state.get(k, default),
    )
    text = handlers._format_switches_text()
    assert "🟢 ON" in text
    assert "🔴 OFF" in text
    # YouTube-пейзд стоит OFF, остальные ON
    lines = text.split("\n")
    # Найдём блок youtube_upload_paused и убедимся что он OFF
    for i, line in enumerate(lines):
        if settings_store.YOUTUBE_UPLOAD_PAUSED_KEY in line:
            assert "OFF" in lines[i + 1]


def test_switches_kb_has_button_per_switch(monkeypatch):
    from src.bot import handlers
    monkeypatch.setattr(
        settings_store, "get_flag", lambda k, default=False: False,
    )
    kb = handlers._switches_kb()
    keys = {k for k, _ in settings_store.SWITCH_KEYS}
    labels = {row[0].text.split(" ", 1)[1] for row in kb.inline_keyboard}
    assert labels == keys


def test_switches_kb_encodes_key_in_callback(monkeypatch):
    from src.bot import handlers
    monkeypatch.setattr(
        settings_store, "get_flag", lambda k, default=False: False,
    )
    kb = handlers._switches_kb()
    for row in kb.inline_keyboard:
        btn = row[0]
        assert btn.callback_data.startswith(f"{handlers.CB_SWITCH}:")
        key = btn.callback_data.split(":", 1)[1]
        assert key in {k for k, _ in settings_store.SWITCH_KEYS}
