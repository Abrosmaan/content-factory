"""APScheduler — расписание всех cron-задач + поллинг-циклы воркеров.

Архитектура:
  • Один AsyncIOScheduler, разделяет event-loop с aiogram-ботом.
  • Cron-jobs (trend_scout */12h, strategist вс, digest вс, ...) — это
    отдельные задачи, синхронные функции воркеров вызываются через
    asyncio.to_thread.
  • Поллинг-jobs (scriptwriter, voice, assembler, thumbnail, seo, publisher)
    с короткими интервалами; каждая стучится в БД через StateMachine.claim
    и при is_paused() возвращается с 0.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.core import settings_store

log = logging.getLogger(__name__)

# Интервалы поллинга воркеров.
POLL_INTERVAL_SECONDS = 15


# ----------- враппер «безопасного» вызова -----------

def _safe_call(fn, *, name: str):
    """Sync→async wrapper: вызывает fn в thread, ловит и логирует ошибки."""
    async def _runner():
        if settings_store.is_paused():
            log.debug("%s skipped (paused)", name)
            return
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.exception("scheduled job %s failed", name)
    _runner.__name__ = f"_runner_{name}"
    return _runner


# ----------- регистрация всех задач -----------

def setup_scheduler() -> AsyncIOScheduler:
    """Создаёт и возвращает уже запущенный планировщик. start() вызывает
    main.py явно после include router'ов."""
    s = AsyncIOScheduler(timezone="UTC")

    # Cron-источники
    from src.workers import topic_strategist, trend_scout
    from src.bot import digest as digest_mod

    # Trend Scout — каждые 12 часов
    s.add_job(
        _safe_call(trend_scout.run_once, name="trend_scout"),
        trigger=IntervalTrigger(hours=12),
        next_run_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2),
        id="trend_scout", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Topic Strategist — воскресенье 06:00 UTC
    s.add_job(
        _safe_call(lambda: topic_strategist.run_once(), name="strategist"),
        trigger=CronTrigger(day_of_week="sun", hour=6, minute=0,
                            timezone="UTC"),
        id="strategist", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Sunday digest — воскресенье 07:00 UTC
    s.add_job(
        _safe_call(lambda: digest_mod.send_digest(), name="digest"),
        trigger=CronTrigger(day_of_week="sun", hour=7, minute=0,
                            timezone="UTC"),
        id="digest", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Daily backup — 04:00 UTC
    from src.cli import backup as backup_cli
    s.add_job(
        _safe_call(lambda: backup_cli.run_once(), name="backup_daily"),
        trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id="backup_daily", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Watchdog — каждые 30 минут
    from src.workers import watchdog
    s.add_job(
        _safe_call(watchdog.run_once, name="watchdog"),
        trigger=IntervalTrigger(minutes=30),
        id="watchdog", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Analyst — ежедневно 05:00 UTC + воскресенье 05:30 (недельный свод)
    from src.workers import analyst
    s.add_job(
        _safe_call(analyst.run_once, name="analyst_daily"),
        trigger=CronTrigger(hour=5, minute=0, timezone="UTC"),
        id="analyst_daily", replace_existing=True,
        max_instances=1, coalesce=True,
    )
    s.add_job(
        _safe_call(analyst.run_once, name="analyst_weekly"),
        trigger=CronTrigger(day_of_week="sun", hour=5, minute=30,
                            timezone="UTC"),
        id="analyst_weekly", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # ----- поллинги воркеров (короткие интервалы) -----
    from src.workers import (
        assembler, footage_generator, publisher, scriptwriter, seo_packager,
        thumbnail, voice,
    )
    poll_jobs = [
        ("scriptwriter",      lambda: scriptwriter.run_once()),
        ("footage_generator", lambda: footage_generator.run_once()),
        ("voice",             lambda: voice.run_once()),
        ("assembler",         lambda: assembler.run_once()),
        ("thumbnail",         lambda: thumbnail.run_once()),
        ("seo_packager",      lambda: seo_packager.run_once()),
        ("publisher",         lambda: publisher.run_once()),
    ]
    for name, fn in poll_jobs:
        s.add_job(
            _safe_call(fn, name=name),
            trigger=IntervalTrigger(seconds=POLL_INTERVAL_SECONDS),
            id=f"poll_{name}", replace_existing=True,
            max_instances=1, coalesce=True,
        )

    log.info("scheduler configured: jobs=%d", len(s.get_jobs()))
    for j in s.get_jobs():
        nxt = getattr(j, "next_run_time", None)
        log.info("  · %s next=%s", j.id, nxt)
    return s


__all__ = ["setup_scheduler", "POLL_INTERVAL_SECONDS"]
