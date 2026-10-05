"""Юнит-тесты card-helpers: парсинг callback_data + клавиатуры."""

from __future__ import annotations

from src.bot import cards


def test_parse_callback_video_approve():
    assert cards.parse_callback("vap:abc-123") == ("vap", "abc-123")


def test_parse_callback_topic_edit():
    assert cards.parse_callback("ted:9d9d") == ("ted", "9d9d")


def test_parse_callback_rejects_empty_id():
    assert cards.parse_callback("vap:") is None
    assert cards.parse_callback("nodelimiter") is None
    assert cards.parse_callback("") is None


def test_video_keyboard_has_three_buttons():
    kb = cards._video_keyboard("v-1")
    btns = kb["inline_keyboard"][0]
    assert [b["callback_data"] for b in btns] == [
        "vap:v-1", "vrj:v-1", "ved:v-1",
    ]
    assert len(btns) == 3


def test_topic_keyboard_has_three_buttons():
    kb = cards._topic_keyboard("t-1")
    btns = kb["inline_keyboard"][0]
    assert [b["callback_data"] for b in btns] == [
        "tap:t-1", "trj:t-1", "ted:t-1",
    ]
