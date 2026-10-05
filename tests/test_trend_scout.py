"""Юнит-тесты trend scout: нормализация / dedup_hash / парсинг RSS."""

from __future__ import annotations

import pytest

from src.workers import trend_scout as ts


def test_normalize_title_lowercases_and_strips_punct():
    assert ts.normalize_title("CS2: Why Hit-Reg FEELS different?!") == \
        "cs2 why hit reg feels different"


def test_normalize_collapses_whitespace():
    assert ts.normalize_title("Best 240Hz   monitor — under $400") == \
        "best 240hz monitor under 400"


def test_dedup_hash_stable_for_punctuation_variants():
    a = ts.dedup_hash("CS2: Why Hit-Reg FEELS different?!")
    b = ts.dedup_hash("cs2 why hit reg feels different")
    assert a == b


def test_dedup_hash_differs_for_different_titles():
    a = ts.dedup_hash("Best 27-inch OLED monitor 2026")
    b = ts.dedup_hash("Worst 27-inch OLED monitor 2026")
    assert a != b


def test_classify_source_reddit_sub():
    source, where = ts._classify_source("https://www.reddit.com/r/pcgaming/.rss")
    assert source == "reddit:pcgaming"
    assert where == "pcgaming"


def test_classify_source_reddit_case_preserved():
    """Reddit RSS принимает любой регистр; сохраняем оригинал в source."""
    source, where = ts._classify_source(
        "https://www.reddit.com/r/MouseReview/.rss")
    assert source == "reddit:MouseReview"
    assert where == "MouseReview"


def test_classify_source_regular_rss():
    source, where = ts._classify_source("https://www.pcgamer.com/rss/")
    assert source == "rss:www.pcgamer.com"
    assert where == "www.pcgamer.com"


def test_fetch_rss_tags_reddit_payload(monkeypatch):
    """Reddit feed entries получают source=reddit:<sub> и source_kind=reddit_hot."""
    class _Feed:
        entries = [
            {"title": "Reddit hot post", "link": "https://r/x", "summary": "s"},
        ]
        bozo = 0
    monkeypatch.setattr(ts, "_http_get_with_backoff",
                        lambda url, *, is_reddit: "<rss/>")
    import feedparser
    monkeypatch.setattr(feedparser, "parse", lambda text: _Feed())

    items = ts.fetch_rss(feeds=["https://old.reddit.com/r/buildapc/.rss"],
                         per_feed=5, reddit_delay_sec=0)
    assert len(items) == 1
    it = items[0]
    assert it.source == "reddit:buildapc"
    assert it.payload["subreddit"] == "buildapc"
    assert it.payload["source_kind"] == "reddit_hot"
