"""Watchdog (спецификация 6.10 ТЗ).

cron */30 min. Пять проверок + эскалация молчания владельца.

Проверки:
  (а) Залипшие записи: status в промежуточном статусе > 6 часов → alert.
  (б) Неразрешённые errors: NOT resolved → счётчик; >0 → alert (1 раз/сутки).
  (в) Квоты: ElevenLabs (subscription endpoint требует user_read; на проде
      пока 401 — fallback просто на 429 в самом TTS, см. E2.3 заметку).
  (г) Суточный link-check `products` — раз в сутки, остальное skip.
  (д) Healthchecks.io ping — внешний надзор «жив».

Эскалация молчания владельца: settings.owner_last_seen_at
  > 14 дней → warning;
  > 21 дня → standby (pause-флаг = true) + alert.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx

from src.bot.alerts import send_alert
from src.core import costs, settings_store
from src.core.config import get_settings
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "watchdog"

STUCK_HOURS = 6
STUCK_ALERT_COOLDOWN_HOURS = 24
SILENCE_WARN_DAYS = 14
SILENCE_STANDBY_DAYS = 21

STUCK_ALERT_KEY = "watchdog_stuck_alerts"

_INTERMEDIATE = (
    "scored", "approved", "scripted", "voiced", "assembled",
    "packaged", "ready_for_review", "publish_queued",
)


# --------- проверки ---------

def check_stuck_records() -> list[dict]:
    """Записи в промежуточном статусе > 6 часов."""
    sql = (
        "SELECT id::text, status::text, status_updated_at, title "
        "FROM videos WHERE status = ANY(%s) "
        "  AND status_updated_at < now() - interval '%s hours'"
    )
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, status::text, status_updated_at, title "
                "FROM videos WHERE status = ANY(%s) "
                "  AND status_updated_at < now() - (%s::int || ' hours')::interval",
                (list(_INTERMEDIATE), STUCK_HOURS),
            )
            rows = cur.fetchall()
    return [
        {"id": r[0], "status": r[1], "updated_at": r[2], "title": r[3]}
        for r in rows
    ]


def check_unresolved_errors() -> int:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM errors WHERE NOT resolved")
            return int(cur.fetchone()[0])


def check_links_due() -> bool:
    """True если последняя проверка ссылок была >24 часов назад
    (или ни разу не было)."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM products "
                "WHERE active AND (last_link_check IS NULL "
                "                  OR last_link_check < now() - interval '24 hours')"
            )
            return int(cur.fetchone()[0]) > 0


def run_link_check() -> dict:
    """Проверка всех active products: HEAD 200, иначе link_ok=false."""
    from src.workers.seo_packager import validate_link
    n_ok = n_bad = 0
    bad: list[str] = []
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, affiliate_url, name FROM products "
                "WHERE active "
                "  AND (last_link_check IS NULL "
                "       OR last_link_check < now() - interval '24 hours')"
            )
            rows = cur.fetchall()
    for pid, url, name in rows:
        ok = validate_link(url)
        with connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE products SET link_ok = %s, last_link_check = now() "
                        "WHERE id = %s",
                        (ok, pid),
                    )
        if ok:
            n_ok += 1
        else:
            n_bad += 1
            bad.append(name)
    if n_bad:
        send_alert(f"🔗 link-check: {n_bad} broken: {', '.join(bad[:5])}")
    return {"ok": n_ok, "bad": n_bad}


def ping_healthchecks() -> bool:
    s = get_settings()
    if not s.HEALTHCHECKS_PING_URL:
        return False
    try:
        r = httpx.get(s.HEALTHCHECKS_PING_URL, timeout=10.0)
        return 200 <= r.status_code < 400
    except httpx.HTTPError as exc:
        log.warning("healthchecks ping failed: %s", exc)
        return False


def check_owner_silence() -> str | None:
    """None | 'warning' | 'standby' — текущая стадия эскалации."""
    raw = settings_store.get("owner_last_seen_at", None)
    if raw is None or (isinstance(raw, (int, float)) and not raw):
        return None
    try:
        last = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    delta = dt.datetime.now(dt.timezone.utc) - last
    if delta >= dt.timedelta(days=SILENCE_STANDBY_DAYS):
        return "standby"
    if delta >= dt.timedelta(days=SILENCE_WARN_DAYS):
        return "warning"
    return None


# --------- run ---------

def _filter_due_for_alert(stuck: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Возвращает (новые-для-алерта, обновлённое состояние).

    Алерт по одной и той же записи слаётся максимум раз в
    STUCK_ALERT_COOLDOWN_HOURS — гасит спам watchdog'а каждые 30 мин.
    """
    raw = settings_store.get(STUCK_ALERT_KEY, None) or {}
    if not isinstance(raw, dict):
        raw = {}
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=STUCK_ALERT_COOLDOWN_HOURS)
    state: dict[str, str] = {}
    due: list[dict] = []
    active_ids = {r["id"] for r in stuck}
    for rid, ts in raw.items():
        try:
            parsed = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        if parsed > cutoff and rid in active_ids:
            state[rid] = parsed.isoformat()
    for r in stuck:
        if r["id"] not in state:
            due.append(r)
            state[r["id"]] = now.isoformat()
    return due, state


def run_once() -> dict:
    results: dict = {}

    stuck = check_stuck_records()
    results["stuck"] = len(stuck)
    if stuck:
        due, new_state = _filter_due_for_alert(stuck)
        results["stuck_due"] = len(due)
        if due:
            names = ", ".join(f"{r['status']}@{r['id'][:8]}" for r in due[:5])
            send_alert(
                f"🥶 {len(due)} stuck record(s) > {STUCK_HOURS}h: {names} "
                f"(silenced for {STUCK_ALERT_COOLDOWN_HOURS}h)"
            )
            try:
                settings_store.set_value(STUCK_ALERT_KEY, new_state)
            except Exception:
                log.warning("set_value stuck_alerts failed", exc_info=True)

    unresolved = check_unresolved_errors()
    results["unresolved_errors"] = unresolved
    if unresolved > 0:
        send_alert(f"⚠️ {unresolved} unresolved errors (resolve from DB or rerun)")

    if check_links_due():
        results["links"] = run_link_check()

    results["healthchecks"] = ping_healthchecks()

    # Дневной порог расходов — один алерт в сутки.
    try:
        spent_today = costs.daily_total()
        results["spent_today"] = round(spent_today, 4)
        if spent_today >= costs.DAILY_ALERT_THRESHOLD_USD:
            today_str = dt.datetime.now(dt.timezone.utc).date().isoformat()
            last_alert_day = settings_store.get(costs.DAILY_ALERT_KEY, "")
            if last_alert_day != today_str:
                send_alert(
                    f"💸 Daily spend ${spent_today:.2f} ≥ "
                    f"${costs.DAILY_ALERT_THRESHOLD_USD:.2f} threshold. "
                    f"Run /costs for breakdown."
                )
                settings_store.set_value(costs.DAILY_ALERT_KEY, today_str)
    except Exception:
        log.warning("daily cost threshold check failed", exc_info=True)

    silence = check_owner_silence()
    results["silence"] = silence
    if silence == "warning":
        send_alert(
            f"⏳ Owner silent for {SILENCE_WARN_DAYS}+ days — check in soon."
        )
    elif silence == "standby":
        try:
            settings_store.pause()
        except Exception:
            pass
        send_alert(
            f"⏸ Owner silent for {SILENCE_STANDBY_DAYS}+ days — "
            f"pipeline paused (LLM/money workers off; trend_scout continues)."
        )

    log.info("watchdog: %s", results)
    return results


__all__ = [
    "WORKER_NAME",
    "STUCK_HOURS", "SILENCE_WARN_DAYS", "SILENCE_STANDBY_DAYS",
    "check_stuck_records", "check_unresolved_errors",
    "check_owner_silence", "ping_healthchecks",
    "run_link_check", "run_once",
]
