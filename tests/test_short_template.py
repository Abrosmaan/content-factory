"""Юнит-тесты Creatomate template_id-режима + маппинг build_short_modifications."""

from __future__ import annotations

import pytest

from src.core import creatomate
from src.workers import assembler


# ---------- creatomate.create_render template_id ----------

def test_create_render_requires_source_or_template_id():
    with pytest.raises(creatomate.CreatomateError):
        creatomate.create_render()


def test_create_render_rejects_both():
    with pytest.raises(creatomate.CreatomateError):
        creatomate.create_render(source={}, template_id="x")


# ---------- assembler.build_short_modifications ----------

SCRIPT = {
    "hook": "Big claim in one sentence.",
    "sections": [
        {"voice": "Fact one — short."},
        {"voice": "Fact two — short."},
        {"voice": "Fact three — short."},
    ],
    "cta": "Subscribe and watch the long version.",
}


def test_build_short_modifications_maps_all_slots():
    urls = ["https://r2/a.mp4", "https://r2/b.mp4", "https://r2/c.mp4"]
    mods = assembler.build_short_modifications(
        script=SCRIPT, video_urls=urls, music_url="https://r2/music.mp3",
    )
    assert mods["Video-1"] == urls[0]
    assert mods["Video-2"] == urls[1]
    assert mods["Video-3"] == urls[2]
    assert mods["Intro-Text"] == SCRIPT["hook"]
    assert mods["Outro-Text"] == SCRIPT["cta"]
    assert mods["Fact-1"] == SCRIPT["sections"][0]["voice"]
    assert mods["Fact-3"] == SCRIPT["sections"][2]["voice"]
    # Не хватило секций → empty fact
    assert mods["Fact-4"] == ""
    assert mods["Fact-5"] == ""
    assert mods["Music"] == "https://r2/music.mp3"


def test_build_short_modifications_cycles_videos_when_one():
    urls = ["https://r2/single.mp4"]
    mods = assembler.build_short_modifications(
        script=SCRIPT, video_urls=urls, music_url=None,
    )
    assert mods["Video-1"] == urls[0]
    assert mods["Video-2"] == urls[0]
    assert mods["Video-3"] == urls[0]
    assert "Music" not in mods


def test_build_short_modifications_requires_videos():
    with pytest.raises(ValueError):
        assembler.build_short_modifications(
            script=SCRIPT, video_urls=[], music_url=None,
        )


def test_build_short_modifications_truncates_long_voice():
    long_voice = "x" * 300
    script = dict(SCRIPT)
    script["sections"] = [{"voice": long_voice}]
    mods = assembler.build_short_modifications(
        script=script, video_urls=["https://r2/a.mp4"], music_url=None,
    )
    assert len(mods["Fact-1"]) == 200
