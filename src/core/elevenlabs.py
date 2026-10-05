"""ElevenLabs TTS client.

Воркер voice вызывает только `tts()` и `quota_remaining_pct()`. Voice ID
ВСЕГДА читается из ENV (константа 3 раздела 2 ТЗ — подмена запрещена).
Модель — eleven_turbo_v2_5 (раздел 6.4).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core import costs
from src.core.config import require

log = logging.getLogger(__name__)

BASE_URL = "https://api.elevenlabs.io/v1"
MODEL_ID = "eleven_turbo_v2_5"


class ElevenLabsError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    s = require("epic2")
    return {"xi-api-key": s.ELEVENLABS_API_KEY, "accept": "audio/mpeg"}


def tts(text: str, *, voice_id: str | None = None,
        output_format: str = "mp3_44100_128",
        timeout: float = 60.0) -> bytes:
    """Сгенерировать аудио из текста. Voice ID — строго из ENV."""
    s = require("epic2")
    vid = voice_id or s.ELEVENLABS_VOICE_ID
    if not vid:
        raise ElevenLabsError("ELEVENLABS_VOICE_ID not set")
    url = f"{BASE_URL}/text-to-speech/{vid}"
    params = {"output_format": output_format}
    body = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    r = httpx.post(url, params=params, json=body, headers=_headers(), timeout=timeout)
    if r.status_code >= 400:
        raise ElevenLabsError(f"TTS {r.status_code}: {r.text[:300]}")
    costs.record_elevenlabs_tts(len(text or ""), worker="voice")
    return r.content


def subscription() -> dict[str, Any]:
    r = httpx.get(f"{BASE_URL}/user/subscription", headers=_headers(), timeout=20.0)
    if r.status_code >= 400:
        raise ElevenLabsError(f"subscription {r.status_code}: {r.text[:200]}")
    return r.json()


def quota_remaining_pct() -> float:
    """Возвращает % оставшейся месячной квоты символов (0..100)."""
    sub = subscription()
    limit = int(sub.get("character_limit") or 0)
    used = int(sub.get("character_count") or 0)
    if limit <= 0:
        return 100.0
    remaining = max(0, limit - used)
    return (remaining / limit) * 100.0


# --------- Music API (ElevenLabs Music) ---------

def music_compose(prompt: str, *,
                  music_length_ms: int = 30_000,
                  output_format: str = "mp3_44100_128",
                  timeout: float = 180.0) -> bytes:
    """Сгенерировать instrumental track через ElevenLabs Music API.

    POST /v1/music — возвращает audio/mpeg.
    music_length_ms: длительность в мс (минимум 10000, максимум зависит от тарифа).
    """
    if music_length_ms < 10_000:
        raise ValueError("music_length_ms must be ≥ 10000")
    url = f"{BASE_URL}/music"
    params = {"output_format": output_format}
    body = {"prompt": prompt, "music_length_ms": music_length_ms}
    headers = {"xi-api-key": require("epic2").ELEVENLABS_API_KEY,
               "accept": "audio/mpeg", "content-type": "application/json"}
    r = httpx.post(url, params=params, json=body,
                   headers=headers, timeout=timeout)
    if r.status_code >= 400:
        raise ElevenLabsError(f"music {r.status_code}: {r.text[:300]}")
    costs.record_elevenlabs_music(worker="assembler")
    return r.content


_GAMING_MUSIC_BASE = (
    "Energetic electronic instrumental, 130-140 bpm, driving synth bass, "
    "punchy drum machine, modern gaming/setup vibe, cinematic build-ups."
)


def music_for_topic(topic_title: str, *, music_length_ms: int = 30_000) -> bytes:
    """Сгенерировать фоновую дорожку под short-видео исходя из темы."""
    short = (topic_title or "").strip()[:120]
    prompt = (
        f"{_GAMING_MUSIC_BASE} Theme cue: {short or 'pc gaming setup'}. "
        f"No vocals, no melodic vocal stabs."
    )
    return music_compose(prompt, music_length_ms=music_length_ms)
