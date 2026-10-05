"""Юнит-тесты repurposer."""

from __future__ import annotations

from src.core import settings_store
from src.workers import repurposer


def test_channels_for_short_has_pinterest_no_blog(monkeypatch):
    """E8.7: short получает pinterest, blog только для длинных."""
    monkeypatch.setattr(settings_store, "is_threads_enabled", lambda: False)
    out = repurposer._channels_for("short")
    assert out["pinterest"] == "pending"
    assert "blog" not in out
    assert out["threads"] == "skipped"


def test_channels_for_long_gaming(monkeypatch):
    monkeypatch.setattr(settings_store, "is_threads_enabled", lambda: True)
    out = repurposer._channels_for("long_gaming")
    assert out == {"blog": "pending", "pinterest": "pending",
                   "threads": "pending"}


def test_channels_for_long_setup_threads_off(monkeypatch):
    monkeypatch.setattr(settings_store, "is_threads_enabled", lambda: False)
    out = repurposer._channels_for("long_setup")
    assert out["blog"] == "pending"
    assert out["pinterest"] == "pending"
    assert out["threads"] == "skipped"


def test_build_prompt_includes_channels_and_script():
    prompt = repurposer._build_prompt(
        video={
            "title": "Why CS2 hit-reg",
            "script": {"hook": "h", "sections": []},
            "seo": {"description_body": "Body text", "products": []},
        },
        channels=["blog", "pinterest"],
    )
    assert "Why CS2 hit-reg" in prompt
    assert "blog" in prompt and "pinterest" in prompt
    assert '"hook": "h"' in prompt
