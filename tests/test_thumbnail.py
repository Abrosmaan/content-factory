"""Юнит-тесты thumbnail."""

from __future__ import annotations

from src.workers import thumbnail


def test_process_skips_short_format():
    res = thumbnail.process_video("v-short", "short", "Some title")
    assert res == "skipped"


def test_overlay_truncates_to_4_words(monkeypatch):
    monkeypatch.setattr(
        thumbnail.llm,
        "complete_json",
        lambda *a, **kw: {
            "variant_a": "One Two Three Four Five Six",
            "variant_b": "X y z w v u t",
        },
    )
    a, b = thumbnail._overlay_phrases("Some title")
    assert len(a.split()) <= 4
    assert len(b.split()) <= 4
    assert a == "One Two Three Four"
