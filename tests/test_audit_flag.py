"""Юнит-тесты E3.3: флаг api_audit_passed и helper напоминания."""

from __future__ import annotations

import pytest

from src.bot import handlers
from src.core import settings_store


def test_settings_store_audit_helpers_call_correct_key(monkeypatch):
    calls = {}
    monkeypatch.setattr(settings_store, "set_flag",
                        lambda k, v: calls.setdefault("set", (k, v)))
    monkeypatch.setattr(settings_store, "get_flag",
                        lambda k, default=False: True if k == "api_audit_passed" else default)
    settings_store.set_audit_passed(True)
    assert calls["set"] == ("api_audit_passed", True)
    assert settings_store.is_audit_passed() is True


def test_reminder_returns_none_when_audit_passed(monkeypatch):
    monkeypatch.setattr(settings_store, "is_audit_passed", lambda: True)
    monkeypatch.setattr(handlers, "_count_private_published", lambda: 5)
    assert handlers.private_published_reminder() is None


def test_reminder_returns_none_when_no_private(monkeypatch):
    monkeypatch.setattr(settings_store, "is_audit_passed", lambda: False)
    monkeypatch.setattr(handlers, "_count_private_published", lambda: 0)
    assert handlers.private_published_reminder() is None


def test_reminder_returns_text_when_audit_off_and_videos_present(monkeypatch):
    monkeypatch.setattr(settings_store, "is_audit_passed", lambda: False)
    monkeypatch.setattr(handlers, "_count_private_published", lambda: 3)
    out = handlers.private_published_reminder()
    assert out is not None
    assert "3 private video" in out
    assert "/audit on" in out
