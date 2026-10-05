"""Учёт расходов на внешние API.

Воркеры зовут `record(...)` сразу после успешного API-вызова. Никаких
оценок «возможной» стоимости — только то что реально дёрнули.

Цены — приблизительные ставки апреля 2026 (см. PRICING ниже). Если
тариф меняется — правим константы здесь, исторические записи остаются
как были (мы храним amount_usd, а не количество с пересчётом на лету).

Безопасность: record() ловит все исключения внутри — не должен ронять
основной поток воркера если БД временно недоступна.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from src.core.db import connection

log = logging.getLogger(__name__)


# --------- цены (USD) ---------
# Все ставки приблизительные. Источники: pricing pages на апрель 2026.
# Anthropic claude-sonnet-4-5: $3/1M input (вкл. cache_read/creation), $15/1M output.
PRICE_CLAUDE_IN_PER_1M  = 3.00
PRICE_CLAUDE_OUT_PER_1M = 15.00

# ElevenLabs TTS Creator/Pro: ~$0.18 за 1000 chars.
PRICE_ELEVENLABS_TTS_PER_1K_CHARS = 0.18

# ElevenLabs Music: ~$0.30 за трек 30s.
PRICE_ELEVENLABS_MUSIC_PER_TRACK = 0.30

# Creatomate: ~$0.03 за секунду HD video.
PRICE_CREATOMATE_PER_SEC = 0.03

# fal.ai Kling v2 master: ~$0.30 за 5s, ~$0.60 за 10s. Считаем за секунду.
PRICE_FAL_KLING_PER_SEC = 0.06

# fal.ai Flux schnell: ~$0.003 за изображение.
PRICE_FAL_FLUX_PER_IMAGE = 0.003

# Дневной порог для алерта.
DAILY_ALERT_THRESHOLD_USD = 5.00
DAILY_ALERT_KEY = "costs_daily_alert_date"


# --------- write ---------

def record(
    service: str,
    amount_usd: float,
    *,
    units: str | None = None,
    quantity: float | None = None,
    worker: str | None = None,
    video_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """Записать стоимость одного вызова. Безопасно молчит при сбое БД."""
    if amount_usd <= 0:
        return
    try:
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO costs "
                        "(service, amount_usd, units, quantity, worker, "
                        " video_id, job_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (service, Decimal(str(round(amount_usd, 6))),
                         units, quantity, worker, video_id, job_id),
                    )
    except Exception:
        log.warning("costs.record(%s, %.4f) failed", service, amount_usd,
                    exc_info=True)


# --------- price helpers (callers удобнее звать) ---------

def record_anthropic(tokens_in: int, tokens_out: int, *,
                     worker: str | None = None,
                     video_id: str | None = None) -> None:
    amt = (tokens_in * PRICE_CLAUDE_IN_PER_1M
           + tokens_out * PRICE_CLAUDE_OUT_PER_1M) / 1_000_000
    record(
        "anthropic", amt,
        units="tokens_in+out", quantity=tokens_in + tokens_out,
        worker=worker, video_id=video_id,
    )


def record_elevenlabs_tts(chars: int, *, worker: str | None = None,
                          video_id: str | None = None) -> None:
    amt = chars * PRICE_ELEVENLABS_TTS_PER_1K_CHARS / 1000
    record(
        "elevenlabs_tts", amt,
        units="chars", quantity=chars,
        worker=worker, video_id=video_id,
    )


def record_elevenlabs_music(*, worker: str | None = None,
                            video_id: str | None = None) -> None:
    record(
        "elevenlabs_music", PRICE_ELEVENLABS_MUSIC_PER_TRACK,
        units="tracks", quantity=1,
        worker=worker, video_id=video_id,
    )


def record_creatomate(duration_sec: float, *, worker: str | None = None,
                      video_id: str | None = None,
                      job_id: str | None = None) -> None:
    amt = max(0.0, float(duration_sec)) * PRICE_CREATOMATE_PER_SEC
    record(
        "creatomate", amt,
        units="sec", quantity=duration_sec,
        worker=worker, video_id=video_id, job_id=job_id,
    )


def record_fal_kling(duration_sec: float, *, worker: str | None = None,
                     video_id: str | None = None,
                     job_id: str | None = None) -> None:
    amt = max(0.0, float(duration_sec)) * PRICE_FAL_KLING_PER_SEC
    record(
        "fal_kling", amt,
        units="sec", quantity=duration_sec,
        worker=worker, video_id=video_id, job_id=job_id,
    )


def record_fal_flux(images: int = 1, *, worker: str | None = None,
                    video_id: str | None = None) -> None:
    amt = images * PRICE_FAL_FLUX_PER_IMAGE
    record(
        "fal_flux", amt,
        units="images", quantity=images,
        worker=worker, video_id=video_id,
    )


# --------- read / summary ---------

# Понятные ярлыки для сервисов
SERVICE_LABELS = {
    "anthropic":        "Anthropic Claude",
    "elevenlabs_tts":   "ElevenLabs TTS",
    "elevenlabs_music": "ElevenLabs Music",
    "creatomate":       "Creatomate",
    "fal_kling":        "fal.ai (footage)",
    "fal_flux":         "fal.ai (Flux)",
}

# Единицы для красивого вывода
UNIT_LABEL = {
    "anthropic":        "tokens",
    "elevenlabs_tts":   "chars",
    "elevenlabs_music": "tracks",
    "creatomate":       "renders",
    "fal_kling":        "clips",
    "fal_flux":         "images",
}


def summary_window(days: int) -> dict[str, dict[str, Any]]:
    """{service: {amount_usd, quantity, n_calls}} за последние N дней."""
    sql = (
        "SELECT service, "
        "       COALESCE(SUM(amount_usd), 0)::numeric, "
        "       COALESCE(SUM(quantity), 0)::numeric, "
        "       COUNT(*) "
        "FROM costs "
        "WHERE created_at >= now() - (%s::int || ' days')::interval "
        "GROUP BY service"
    )
    out: dict[str, dict[str, Any]] = {}
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (days,))
            for service, amt, qty, n in cur.fetchall():
                out[service] = {
                    "amount_usd": float(amt or 0),
                    "quantity":   float(qty or 0),
                    "n_calls":    int(n or 0),
                }
    return out


def grand_total_window(days: int | None) -> float:
    """Сумма по всем сервисам за N дней. None → за всё время."""
    if days is None:
        sql = "SELECT COALESCE(SUM(amount_usd), 0)::numeric FROM costs"
        params: tuple = ()
    else:
        sql = (
            "SELECT COALESCE(SUM(amount_usd), 0)::numeric "
            "FROM costs "
            "WHERE created_at >= now() - (%s::int || ' days')::interval"
        )
        params = (days,)
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return float(cur.fetchone()[0] or 0)


def daily_total(day: dt.date | None = None) -> float:
    """Сумма расходов за указанную дату (UTC). По умолчанию — сегодня."""
    target = day or dt.datetime.now(dt.timezone.utc).date()
    sql = (
        "SELECT COALESCE(SUM(amount_usd), 0)::numeric "
        "FROM costs "
        "WHERE created_at::date = %s"
    )
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (target,))
            return float(cur.fetchone()[0] or 0)


def format_summary(seven: dict[str, dict[str, Any]],
                   total_7d: float,
                   total_30d: float,
                   total_all: float) -> str:
    """Готовый текст для /costs (под формат из ТЗ)."""
    lines = ["💰 Cost Dashboard — last 7 days", ""]
    for svc, label in SERVICE_LABELS.items():
        d = seven.get(svc) or {"amount_usd": 0.0, "quantity": 0.0,
                               "n_calls": 0}
        unit = UNIT_LABEL.get(svc, "calls")
        qty_int = int(d["quantity"])
        lines.append(f"{label}: ${d['amount_usd']:.2f} ({qty_int} {unit})")
    lines.append("─────────────────")
    lines.append(f"Total: ${total_7d:.2f}")
    lines.append("")
    lines.append(f"За 30 дней: ${total_30d:.2f}")
    lines.append(f"За всё время: ${total_all:.2f}")
    return "\n".join(lines)


__all__ = [
    "PRICE_CLAUDE_IN_PER_1M", "PRICE_CLAUDE_OUT_PER_1M",
    "PRICE_ELEVENLABS_TTS_PER_1K_CHARS",
    "PRICE_ELEVENLABS_MUSIC_PER_TRACK",
    "PRICE_CREATOMATE_PER_SEC",
    "PRICE_FAL_KLING_PER_SEC", "PRICE_FAL_FLUX_PER_IMAGE",
    "DAILY_ALERT_THRESHOLD_USD", "DAILY_ALERT_KEY",
    "record",
    "record_anthropic", "record_elevenlabs_tts", "record_elevenlabs_music",
    "record_creatomate", "record_fal_kling", "record_fal_flux",
    "summary_window", "grand_total_window", "daily_total", "format_summary",
]
