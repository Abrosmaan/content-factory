"""Юнит-тесты voice: сборка реплик, парсинг loudnorm JSON, обёртка над TTS."""

from __future__ import annotations

import pytest

from src.core import audio
from src.workers import voice


def test_utterances_includes_hook_sections_cta():
    script = {
        "hook": "H",
        "sections": [
            {"voice": "a", "footage_tag": "menu"},
            {"voice": "b", "footage_tag": "combat"},
        ],
        "cta": "C",
    }
    assert voice._utterances(script) == ["H", "a", "b", "C"]


def test_utterances_skips_empty_voices():
    script = {
        "hook": "",
        "sections": [{"voice": None}, {"voice": "ok"}],
        "cta": "C",
    }
    assert voice._utterances(script) == ["ok", "C"]


def test_loudnorm_parser_extracts_input_i():
    stderr = (
        "ffmpeg banner...\n"
        '{\n  "input_i" : "-23.5",\n  "input_tp" : "-3.2",\n'
        '  "input_lra" : "11.0",\n  "input_thresh" : "-33.6",\n'
        '  "target_offset" : "1.5"\n}\n'
        "more output\n"
    )
    data = audio._parse_loudnorm_json(stderr)
    assert data["input_i"] == "-23.5"
    assert data["target_offset"] == "1.5"


def test_loudnorm_parser_raises_when_absent():
    with pytest.raises(RuntimeError):
        audio._parse_loudnorm_json("ffmpeg output without json")
