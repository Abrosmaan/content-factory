"""Voice worker (спецификация 6.4 ТЗ).

Цикл:
  1. claim('scripted')
  2. собрать script (hook + sections + cta) в плоский список реплик
  3. ElevenLabs TTS каждой реплики → mp3 в /tmp
  4. ffmpeg concat → один mp3
  5. ffmpeg loudnorm (two-pass) → -16 LUFS
  6. upload в R2_BUCKET_RENDERS
  7. update voice_r2_key, transition scripted → voiced
  8. побочно: проверить остаток квоты ElevenLabs, если < 20% → Telegram-alert

Voice ID — строго из ELEVENLABS_VOICE_ID (раздел 2 ТЗ, константа 3).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from src.bot.alerts import send_alert
from src.core import audio, elevenlabs, r2
from src.core.config import require
from src.core.db import connection
from src.core.retry import RetryConfig, run_with_retry
from src.core.state_machine import Claimed, StateMachine

log = logging.getLogger(__name__)

WORKER_NAME = "voice"
QUOTA_WARN_PCT = 20.0


def _fetch_script(conn, video_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT script FROM videos WHERE id = %s", (video_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _save_voice_key(conn, video_id: str, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET voice_r2_key = %s WHERE id = %s",
            (key, video_id),
        )


def _save_script(conn, video_id: str, script: dict[str, Any]) -> None:
    import json as _json
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE videos SET script = %s::jsonb WHERE id = %s",
            (_json.dumps(script), video_id),
        )


def _utterances(script: dict[str, Any]) -> list[str]:
    """Собрать порядок реплик: hook → sections.voice (в порядке) → cta."""
    out: list[str] = []
    if script.get("hook"):
        out.append(script["hook"])
    for s in script.get("sections", []) or []:
        v = (s or {}).get("voice")
        if v:
            out.append(v)
    if script.get("cta"):
        out.append(script["cta"])
    return out


def _synth_one(text: str) -> bytes:
    cfg = RetryConfig(
        worker=WORKER_NAME,
        non_retryable=(),  # ElevenLabs ошибки лучше ретраить — quota/429
    )
    return run_with_retry(lambda: elevenlabs.tts(text), cfg)


def process_video(claimed: Claimed) -> str:
    video_id = claimed.video_id
    log.info("voice start video=%s", video_id)
    require("epic2")  # явная проверка ключей перед денежным вызовом

    with connection() as conn:
        script = _fetch_script(conn, video_id)
    if not script:
        StateMachine(WORKER_NAME).transition(
            video_id, "scripted", "failed",
            last_error="no script jsonb on voiced claim",
            detail="empty script",
        )
        return "failed"

    utts = _utterances(script)
    if not utts:
        StateMachine(WORKER_NAME).transition(
            video_id, "scripted", "failed",
            last_error="script has no utterances",
        )
        return "failed"

    with tempfile.TemporaryDirectory(prefix=f"voice-{video_id}-") as td:
        tmp = Path(td)
        parts: list[Path] = []
        durations: list[float] = []
        for i, text in enumerate(utts):
            data = _synth_one(text)
            p = tmp / f"part-{i:02d}.mp3"
            p.write_bytes(data)
            parts.append(p)
            durations.append(audio.duration_sec(p))

        joined = tmp / "joined.mp3"
        audio.concat(parts, joined)

        normalized = tmp / "final.mp3"
        audio.normalize(joined, normalized)

        # Уровень после нормализации — для AC -16±1 LUFS
        out_lufs = audio.measure_lufs(normalized)
        total_dur = audio.duration_sec(normalized)
        log.info("voice video=%s out=%.2f LUFS total=%.1fs",
                 video_id, out_lufs, total_dur)

        bucket = require("epic1").R2_BUCKET_RENDERS
        key = f"voice/{video_id}.mp3"
        r2.upload_file(bucket, key, str(normalized), content_type="audio/mpeg")

    # Прокинуть длительности обратно в script — assembler использует их
    # для таймингов клипов и subtitle. utts собран как hook + sections + cta,
    # значит durations[0]=hook, durations[1..-2]=sections, durations[-1]=cta.
    script["hook_duration_sec"] = durations[0] if durations else 0.0
    script["cta_duration_sec"] = durations[-1] if len(durations) > 1 else 0.0
    section_durs = durations[1:-1] if len(durations) > 2 else durations[1:]
    for i, s in enumerate(script.get("sections") or []):
        if i < len(section_durs):
            s["voice_duration_sec"] = section_durs[i]
    script["total_voice_duration_sec"] = total_dur

    with connection() as conn:
        with conn.transaction():
            _save_voice_key(conn, video_id, key)
            _save_script(conn, video_id, script)

    detail = f"utterances={len(utts)} out_lufs={out_lufs:.2f} total={total_dur:.1f}s"
    # E8.5: voice теперь стартует от footage_ready (footage_generator уже
    # отработал для short/long_setup; для long_gaming scriptwriter перевёл
    # сам в footage_ready после scripted).
    StateMachine(WORKER_NAME).transition(
        video_id, "footage_ready", "voiced", detail=detail,
    )

    # Контроль квоты после успешной озвучки
    try:
        pct = elevenlabs.quota_remaining_pct()
        log.info("elevenlabs quota remaining: %.1f%%", pct)
        if pct < QUOTA_WARN_PCT:
            send_alert(
                f"⚠️ ElevenLabs квота: осталось {pct:.1f}% символов в месяце."
            )
    except Exception:
        log.warning("quota check failed", exc_info=True)

    return "voiced"


def run_once(*, limit: int = 1) -> int:
    sm = StateMachine(WORKER_NAME)
    claimed = sm.claim("footage_ready", limit=limit)
    processed = 0
    for c in claimed:
        try:
            process_video(c)
            processed += 1
        except Exception:
            log.exception("voice failed video=%s", c.video_id)
    return processed


__all__ = ["WORKER_NAME", "process_video", "run_once"]
