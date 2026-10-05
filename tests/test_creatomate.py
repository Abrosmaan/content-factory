"""Юнит-тесты: загрузка JSON-шаблонов и базовая структура.

Реальный тест-рендер делается отдельно (smoke на VPS, E2.4 AC) и
подтверждается вручную через CONTENT_FACTORY_TASKS.md.
"""

from __future__ import annotations

import pytest

from src.core import creatomate


REQUIRED_TOP_LEVEL = {"output_format", "frame_rate", "width", "height", "elements"}
REQUIRED_SLOTS_PER_TEMPLATE = {
    "short_v1":  {"footage", "voice", "subtitles", "music"},
    "long_v1":   {"footage_0", "voice", "subtitles", "music"},
    "setup_v1":  {"footage_0", "voice", "subtitles", "music"},
}


@pytest.mark.parametrize("name", ["short_v1", "long_v1", "setup_v1"])
def test_template_loads(name):
    t = creatomate.load_template(name)
    missing = REQUIRED_TOP_LEVEL - t.keys()
    assert not missing, f"{name} missing keys: {missing}"
    assert isinstance(t["elements"], list) and t["elements"]


@pytest.mark.parametrize("name,slots", REQUIRED_SLOTS_PER_TEMPLATE.items())
def test_template_has_named_slots(name, slots):
    t = creatomate.load_template(name)
    ids = {e.get("id") for e in t["elements"] if e.get("id")}
    missing = slots - ids
    assert not missing, f"{name} missing slots: {missing}"


def test_short_is_vertical_for_shorts():
    t = creatomate.load_template("short_v1")
    assert t["width"] < t["height"], "short_v1 must be portrait"


@pytest.mark.parametrize("name", ["long_v1", "setup_v1"])
def test_long_is_landscape(name):
    t = creatomate.load_template(name)
    assert t["width"] > t["height"], f"{name} must be landscape"
