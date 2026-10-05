"""Юнит-тесты SEO packager: формат таймкодов, chapters, gear, FTC,
link-validation (через respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.workers import seo_packager as seo


def test_format_ts():
    assert seo._format_ts(0) == "0:00"
    assert seo._format_ts(8) == "0:08"
    assert seo._format_ts(75) == "1:15"
    assert seo._format_ts(601) == "10:01"


def test_chapters_from_script_durations():
    script = {
        "hook_duration_sec": 5.0,
        "sections": [
            {"id": "intro", "voice_duration_sec": 30.0},
            {"id": "fix-positioning", "voice_duration_sec": 60.0},
        ],
        "cta_duration_sec": 5.0,
    }
    ch = seo._build_chapters(script)
    assert ch[0] == {"time": "0:00", "title": "Intro"}
    # после hook 5s → intro section starts at 0:05
    assert ch[1] == {"time": "0:05", "title": "Intro"}
    assert ch[2] == {"time": "0:35", "title": "Fix Positioning"}


def test_format_gear_includes_price_and_link():
    out = seo._format_gear([
        {"name": "Mouse A", "affiliate_url": "https://x/a", "price_usd": 89.0},
        {"name": "KB B",    "affiliate_url": "https://x/b", "price_usd": None},
    ])
    assert "Mouse A" in out and "$89" in out and "https://x/a" in out
    assert "KB B" in out and "https://x/b" in out


def test_full_description_includes_ftc_and_chapters():
    chapters = [{"time": "0:00", "title": "Intro"}]
    products = [{"name": "X", "affiliate_url": "https://x", "price_usd": 10}]
    out = seo._full_description("Body text.", chapters, products)
    assert "Body text." in out
    assert "Chapters:" in out
    assert "0:00" in out
    assert "Gear in this video:" in out
    assert seo.FTC_LINE in out


def test_full_description_omits_gear_when_no_products():
    out = seo._full_description("Body.", [{"time": "0:00", "title": "Intro"}], [])
    assert "Gear in this video:" not in out
    assert seo.FTC_LINE not in out


def test_full_description_includes_ai_disclosure_when_flag():
    out = seo._full_description(
        "Body.", [{"time": "0:00", "title": "Intro"}], [],
        ai_disclosure=True,
    )
    assert seo.AI_DISCLOSURE_LINE in out


def test_full_description_omits_ai_disclosure_by_default():
    out = seo._full_description(
        "Body.", [{"time": "0:00", "title": "Intro"}], [],
    )
    assert seo.AI_DISCLOSURE_LINE not in out


def test_needs_ai_disclosure_predicate():
    assert seo.needs_ai_disclosure("short") is True
    assert seo.needs_ai_disclosure("long_setup") is True
    assert seo.needs_ai_disclosure("long_gaming") is False
    assert seo.needs_ai_disclosure("bridge") is False


@respx.mock
def test_validate_link_200_via_head():
    respx.head("https://x.test/a").mock(return_value=httpx.Response(200))
    assert seo.validate_link("https://x.test/a") is True


@respx.mock
def test_validate_link_fallback_to_get_on_405():
    respx.head("https://x.test/b").mock(return_value=httpx.Response(405))
    respx.get("https://x.test/b").mock(return_value=httpx.Response(200))
    assert seo.validate_link("https://x.test/b") is True


@respx.mock
def test_validate_link_returns_false_on_404():
    respx.head("https://x.test/c").mock(return_value=httpx.Response(404))
    respx.get("https://x.test/c").mock(return_value=httpx.Response(404))
    assert seo.validate_link("https://x.test/c") is False


@respx.mock
def test_validate_link_returns_false_on_network_error():
    respx.head("https://x.test/d").mock(side_effect=httpx.ConnectError("nope"))
    assert seo.validate_link("https://x.test/d") is False
