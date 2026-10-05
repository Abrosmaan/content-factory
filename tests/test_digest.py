"""Юнит-тесты digest: сборка summary текста без реальной БД."""

from __future__ import annotations

from src.bot import digest


def test_summary_includes_published_count(monkeypatch):
    monkeypatch.setattr(digest, "_published_last_week", lambda: 3)
    monkeypatch.setattr(digest, "_format_counts",
                        lambda: {"ready_for_review": 2, "pending_approval": 5,
                                 "scripted": 1, "voiced": 1})
    monkeypatch.setattr(digest, "_unresolved_errors", lambda: 0)
    monkeypatch.setattr(digest, "_footage_gap_recent", lambda: [])
    monkeypatch.setattr(digest, "private_published_reminder", lambda: None)
    out = digest.build_summary()
    assert "published last 7d" in out
    assert "<b>3</b>" in out
    assert "ready for review: 2" in out
    assert "pending topics: 5" in out
    assert "errors unresolved: 0" in out
    assert "epic 6" in out  # distribution stub


def test_summary_appends_footage_gaps(monkeypatch):
    monkeypatch.setattr(digest, "_published_last_week", lambda: 0)
    monkeypatch.setattr(digest, "_format_counts", lambda: {})
    monkeypatch.setattr(digest, "_unresolved_errors", lambda: 2)
    monkeypatch.setattr(digest, "_footage_gap_recent",
                        lambda: ["footage_gap: cs2/combat",
                                 "footage_gap: valorant/menu"])
    monkeypatch.setattr(digest, "private_published_reminder", lambda: None)
    out = digest.build_summary()
    assert "footage gaps: 2" in out
    assert "cs2/combat" in out
    assert "valorant/menu" in out


def test_summary_appends_private_reminder(monkeypatch):
    monkeypatch.setattr(digest, "_published_last_week", lambda: 0)
    monkeypatch.setattr(digest, "_format_counts", lambda: {})
    monkeypatch.setattr(digest, "_unresolved_errors", lambda: 0)
    monkeypatch.setattr(digest, "_footage_gap_recent", lambda: [])
    monkeypatch.setattr(digest, "private_published_reminder",
                        lambda: "3 private videos still on the channel.")
    out = digest.build_summary()
    assert "3 private videos" in out
    assert "🔒" in out
