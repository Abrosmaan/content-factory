"""Юнит-тесты publishers E6.4/5/6: чистая логика."""

from __future__ import annotations

import json

from src.workers import blog_publisher as bp
from src.workers import pinterest_publisher as pp


def test_blog_frontmatter_contains_required_fields():
    out = bp.build_frontmatter(
        title="Setup guide", date="2026-06-12",
        video_id="vid-1", youtube_id="abc123",
        products=[{"name": "X", "url": "https://x", "price_usd": 99}],
    )
    assert out.startswith("---\n")
    assert "Setup guide" in out
    assert "vid-1" in out
    assert "abc123" in out
    assert "https://x" in out


def test_blog_frontmatter_omits_youtube_when_absent():
    out = bp.build_frontmatter(
        title="t", date="2026-06-12", video_id="v",
        youtube_id=None, products=[],
    )
    assert "youtube_url" not in out


def test_blog_frontmatter_skips_products_without_url():
    out = bp.build_frontmatter(
        title="t", date="2026-06-12", video_id="v",
        youtube_id=None,
        products=[
            {"name": "ok", "url": "https://x"},
            {"name": "bad"},  # no url
        ],
    )
    data = json.loads(out.strip("-\n "))
    assert len(data["products"]) == 1


def test_pinterest_board_picker(monkeypatch):
    class _S:
        PINTEREST_BOARD_ID_SETUP = "board-setup"
        PINTEREST_BOARD_ID_GAMING = "board-gaming"
    monkeypatch.setattr(pp, "require", lambda _stage: _S())
    assert pp._board_for("long_setup") == "board-setup"
    assert pp._board_for("long_gaming") == "board-gaming"
    assert pp._board_for("short") == "board-gaming"
