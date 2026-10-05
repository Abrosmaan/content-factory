"""Юнит-тесты чистых helper'ов /note: extract_keywords + infer_format."""

from __future__ import annotations

from src.bot import handlers


def test_extract_keywords_basic():
    out = handlers.extract_keywords(
        "купить механическую клавиатуру под $100"
    )
    # 'купить' и 'под' в стоп-словах
    assert "механическую" in out
    assert "клавиатуру" in out
    assert "$100" in out
    assert "купить" not in out
    assert "под" not in out


def test_extract_keywords_dedupes_and_lowercases():
    out = handlers.extract_keywords("Razer Razer RAZER mouse Mouse")
    # должен схлопнуться до 2 уникальных токенов
    assert out == ["razer", "mouse"]


def test_extract_keywords_max_n():
    note = " ".join(f"token{i}" for i in range(50))
    out = handlers.extract_keywords(note, max_n=10)
    assert len(out) == 10


def test_extract_keywords_empty():
    assert handlers.extract_keywords("") == []
    assert handlers.extract_keywords("и в на под") == []  # одни стоп-слова


def test_infer_format_default_is_long_setup():
    assert handlers.infer_format("обзор клавиатуры") == "long_setup"
    assert handlers.infer_format("") == "long_setup"


def test_infer_format_short_keywords():
    assert handlers.infer_format("сделать short про мышь") == "short"
    assert handlers.infer_format("idea for tiktok") == "short"
    assert handlers.infer_format("youtube shorts") == "short"


def test_infer_format_bridge():
    assert handlers.infer_format("bridge видео про CS2") == "bridge"
