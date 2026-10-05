"""Юнит-тесты scriptwriter: валидация структуры, fact-check блокеры,
сборка промпта."""

from __future__ import annotations

import pytest

from src.workers import scriptwriter as sw


# ---------- validation ----------

def test_validate_short_caps_sections():
    bad = {
        "title": "ok", "hook": "hello", "cta": "subscribe",
        "sections": [{"voice": f"v{i}", "footage_tag": "menu"} for i in range(5)],
    }
    with pytest.raises(sw.InvalidScript):
        sw._validate_script(bad, "short")


def test_validate_long_caps_sections():
    sections = [{"voice": f"v{i}", "footage_tag": "menu"} for i in range(8)]
    bad = {"title": "ok", "hook": "h", "cta": "c", "sections": sections}
    with pytest.raises(sw.InvalidScript):
        sw._validate_script(bad, "long_gaming")


def test_validate_title_length_limit():
    bad = {"title": "x" * 80, "hook": "h", "cta": "c",
           "sections": [{"voice": "v", "footage_tag": "menu"}]}
    with pytest.raises(sw.InvalidScript):
        sw._validate_script(bad, "short")


def test_validate_missing_key():
    with pytest.raises(sw.InvalidScript):
        sw._validate_script({"hook": "h"}, "short")


def test_validate_section_must_have_voice_and_tag():
    bad = {"title": "ok", "hook": "h", "cta": "c",
           "sections": [{"voice": "v"}]}
    with pytest.raises(sw.InvalidScript):
        sw._validate_script(bad, "short")


def test_validate_short_ok():
    ok = {"title": "ok", "hook": "h", "cta": "c",
          "sections": [{
              "voice": "v", "footage_tag": "menu",
              "scene_description": "cinematic close-up of mouse on desk, neon lighting"
          }]}
    sw._validate_script(ok, "short")


def test_validate_long_gaming_ok_without_scene_description():
    ok = {
        "title": "ok",
        "hook": "h",
        "cta": "c",
        "sections": [
            {"voice": f"v{i}", "footage_tag": "combat"} for i in range(5)
        ],
    }
    sw._validate_script(ok, "long_gaming")


def test_validate_short_rejects_missing_scene_description():
    bad = {"title": "ok", "hook": "h", "cta": "c",
           "sections": [{"voice": "v", "footage_tag": "menu"}]}
    with pytest.raises(sw.InvalidScript) as ei:
        sw._validate_script(bad, "short")
    assert "scene_description" in str(ei.value)


def test_validate_long_setup_rejects_missing_scene_description():
    bad = {"title": "ok", "hook": "h", "cta": "c",
           "sections": [
               {"voice": f"v{i}", "footage_tag": "peripheral"} for i in range(4)
           ]}
    with pytest.raises(sw.InvalidScript) as ei:
        sw._validate_script(bad, "long_setup")
    assert "scene_description" in str(ei.value)


def test_validate_long_gaming_does_not_require_scene_description():
    """Для long_gaming scene_description опционален — банк сам решает."""
    ok = {"title": "ok", "hook": "h", "cta": "c",
          "sections": [
              {"voice": f"v{i}", "footage_tag": "combat"} for i in range(4)
          ]}
    sw._validate_script(ok, "long_gaming")  # без scene_description — ok


def test_uses_ai_footage_predicate():
    assert sw.uses_ai_footage("short") is True
    assert sw.uses_ai_footage("long_setup") is True
    assert sw.uses_ai_footage("long_gaming") is False
    assert sw.uses_ai_footage("bridge") is False


# ---------- fact-check ----------

def test_high_wrong_is_blocker():
    fc = {"issues": [{"severity": "high", "verdict": "wrong", "claim": "RTX 5090 has 64GB VRAM"}]}
    blocking, claim = sw._has_blocking_issue(fc)
    assert blocking is True
    assert "RTX 5090" in claim


def test_high_uncertain_not_blocker():
    fc = {"issues": [{"severity": "high", "verdict": "uncertain"}]}
    blocking, _ = sw._has_blocking_issue(fc)
    assert blocking is False


def test_low_wrong_not_blocker():
    fc = {"issues": [{"severity": "low", "verdict": "wrong"}]}
    blocking, _ = sw._has_blocking_issue(fc)
    assert blocking is False


def test_empty_issues_passes():
    assert sw._has_blocking_issue({"issues": []})[0] is False
    assert sw._has_blocking_issue({})[0] is False


# ---------- prompt assembly ----------

def test_script_prompt_includes_topic_and_games():
    topic = sw.TopicData(id="t1", title="Best CS2 maps 2026",
                         url="https://example.com", score_reasoning="trending")
    prompt = sw._build_script_prompt(
        fmt="long_gaming",
        topic=topic,
        games=["cs2", "valorant"],
        products=[{"id": "p1", "name": "Mouse X", "category": "mouse",
                   "affiliate_url": "u", "commission_pct": 5.0, "price_usd": 89.0}],
    )
    assert "long_gaming" in prompt
    assert "Best CS2 maps 2026" in prompt
    assert "cs2, valorant" in prompt
    assert "Mouse X" in prompt
    assert "p1" in prompt


def test_short_prompt_omits_products_block():
    topic = sw.TopicData(id="t1", title="Q", url=None, score_reasoning=None)
    prompt = sw._build_script_prompt(
        fmt="short", topic=topic, games=["cs2"], products=[],
    )
    # для шортсов products_rule велит products_used=[]
    assert "Leave `products_used` as []" in prompt
    assert "products_used" in prompt


def test_products_block_with_empty_list():
    assert "No product integrations" in sw._products_block([])
