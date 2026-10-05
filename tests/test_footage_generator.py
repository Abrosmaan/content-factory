"""Юнит-тесты footage_generator: generate_section retry + fallback path."""

from __future__ import annotations

from src.workers import footage_generator as fg


def test_generate_section_succeeds_first_kling(monkeypatch):
    monkeypatch.setattr(fg, "_gen_kling", lambda p, duration=5: "https://x/clip.mp4")
    monkeypatch.setattr(fg, "_download_to_r2",
                        lambda url, bucket, key, content_type="video/mp4": 12345)
    key, src = fg.generate_section("scene", video_id="v-1", idx=0, bucket="renders")
    assert key == "generated/v-1/00.mp4"
    assert src == "kling"


def test_generate_section_falls_back_after_3_kling_failures(monkeypatch):
    calls = {"kling": 0, "fallback": 0, "alerts": []}

    def _kling_fail(p, duration=5):
        calls["kling"] += 1
        raise RuntimeError("content policy")

    def _fallback(p, *, video_id, idx):
        calls["fallback"] += 1
        return "https://x/fallback.mp4"

    monkeypatch.setattr(fg, "_gen_kling", _kling_fail)
    monkeypatch.setattr(fg, "_gen_fallback_kenburns", _fallback)
    monkeypatch.setattr(fg, "_download_to_r2",
                        lambda url, bucket, key, content_type="video/mp4": 999)
    monkeypatch.setattr(fg, "send_alert", lambda msg: calls["alerts"].append(msg))

    key, src = fg.generate_section("scene", video_id="v-2", idx=1, bucket="renders")
    assert key == "generated/v-2/01.mp4"
    assert src == "fallback"
    assert calls["kling"] == 3
    assert calls["fallback"] == 1
    assert calls["alerts"] and "Ken Burns" in calls["alerts"][0]


def test_generate_section_raises_when_both_fail(monkeypatch):
    monkeypatch.setattr(fg, "_gen_kling",
                        lambda p, duration=5: (_ for _ in ()).throw(RuntimeError("kling down")))
    monkeypatch.setattr(fg, "_gen_fallback_kenburns",
                        lambda p, video_id, idx: (_ for _ in ()).throw(RuntimeError("flux down")))
    import pytest
    with pytest.raises(RuntimeError) as ei:
        fg.generate_section("scene", video_id="v-3", idx=0, bucket="r")
    assert "both Kling" in str(ei.value)
