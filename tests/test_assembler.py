"""Юнит-тесты assembler: подбор footage, footage_gap, сборка modifications."""

from __future__ import annotations

import pytest

from src.workers import assembler


def test_subtitles_concat_orders_hook_sections_cta():
    script = {
        "hook": "H",
        "sections": [{"voice": "a"}, {"voice": "b"}],
        "cta": "C",
    }
    assert assembler._subtitles_concat(script) == "H a b C"


def test_subtitles_skips_empty():
    script = {"hook": "", "sections": [{"voice": None}, {"voice": "ok"}], "cta": "C"}
    assert assembler._subtitles_concat(script) == "ok C"


def test_template_name_maps_format():
    assert assembler._template_name("short") == "short_v1"
    assert assembler._template_name("long_gaming") == "long_v1"
    assert assembler._template_name("long_setup") == "setup_v1"
    assert assembler._template_name("bridge") == "long_v1"
    assert assembler._template_name("unknown") == "long_v1"


def test_footage_gap_carries_missing_descriptors():
    err = assembler.FootageGap(["cs2/combat", "valorant/menu"])
    assert "cs2/combat" in str(err)
    assert "valorant/menu" in err.missing


def test_build_modifications_one_slot(monkeypatch):
    # для short_v1 один слот footage
    from src.core import creatomate, r2
    template = creatomate.load_template("short_v1")
    monkeypatch.setattr(r2, "signed_url", lambda b, k, ttl_seconds=0: f"signed://{b}/{k}")
    picks = [
        assembler.Footage(id="f1", r2_key="cs2/clip1.mp4", game="cs2",
                          tags=["combat"], duration_sec=8),
    ]
    mods = assembler._build_modifications(
        fmt="short", template=template,
        footage_picks=picks,
        voice_url="signed://renders/voice.mp3",
        voice_duration=20.0,
        section_durations=[18.0],
        subtitles_text="hello world",
        music_url=None,
    )
    assert mods["footage.source"] == "signed://footage/cs2/clip1.mp4"
    assert mods["footage.duration"] == 20.0
    assert mods["voice.duration"] == 20.0
    assert mods["subtitles.text"] == "hello world"
    assert "music.source" not in mods


def test_build_modifications_uses_generated_clips_when_provided(monkeypatch):
    """E8.5: для AI-форматов берём generated_clip_urls вместо банка."""
    from src.core import creatomate, r2
    template = creatomate.load_template("short_v1")
    monkeypatch.setattr(r2, "signed_url",
                        lambda b, k, ttl_seconds=0: pytest.fail(
                            "must not sign anything when generated_clip_urls provided"))
    mods = assembler._build_modifications(
        fmt="short", template=template,
        footage_picks=[],  # banк не используется
        voice_url="vurl", voice_duration=12.0,
        section_durations=[10.0],
        subtitles_text="hello",
        music_url=None,
        generated_clip_urls=["https://r2/generated/v/00.mp4"],
    )
    assert mods["footage.source"] == "https://r2/generated/v/00.mp4"
    assert mods["footage.duration"] == 12.0


def test_uses_generated_clips_predicate():
    assert assembler.uses_generated_clips("short") is True
    assert assembler.uses_generated_clips("long_setup") is True
    assert assembler.uses_generated_clips("long_gaming") is False
    assert assembler.uses_generated_clips("bridge") is False


def test_build_modifications_multi_slot_splits_evenly(monkeypatch):
    from src.core import creatomate, r2
    template = creatomate.load_template("long_v1")
    monkeypatch.setattr(r2, "signed_url", lambda b, k, ttl_seconds=0: f"u://{k}")
    picks = [
        assembler.Footage(id="f1", r2_key="a.mp4", game="cs2", tags=[], duration_sec=10),
        assembler.Footage(id="f2", r2_key="b.mp4", game="cs2", tags=[], duration_sec=10),
        assembler.Footage(id="f3", r2_key="c.mp4", game="cs2", tags=[], duration_sec=10),
    ]
    mods = assembler._build_modifications(
        fmt="long_gaming", template=template,
        footage_picks=picks,
        voice_url="vurl", voice_duration=300.0,
        section_durations=[100, 100, 100],
        subtitles_text="t",
        music_url="murl",
    )
    assert mods["footage_0.duration"] == 100
    assert mods["footage_1.time"] == 100
    assert mods["footage_2.time"] == 200
    assert mods["music.source"] == "murl"
