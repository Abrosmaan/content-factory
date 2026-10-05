"""Trend Scout (спецификация 6.1 ТЗ; AL-002 — переход с Reddit API на RSS).

cron */12h: RSS-фиды (gaming-новости + Reddit-сабы через публичные `.rss`)
→ topics с dedup_hash = sha256(normalize(title)). ON CONFLICT DO NOTHING —
повторный прогон не создаёт дублей. LLM не используется.

Reddit-сабы парсятся как обычные RSS-фиды; authorization не нужна. Source
помечается `reddit:<sub>` для тех URL, host которых www.reddit.com, чтобы
Strategist (E4.2) мог приоритизировать Reddit-посты в `hot` ленте (там
обычно >100 upvotes по алгоритму Reddit).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from src.core.db import connection
from src.core.trend_sources import RSS_FEEDS, RSS_PER_FEED

log = logging.getLogger(__name__)

WORKER_NAME = "trend_scout"
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")

# User-Agent для Reddit: они отдают 429/403 на пустых/дефолтных UA.
_USER_AGENT = "content-factory:trend-scout:0.2 (RSS reader)"


# --------- normalization + dedup hash ---------

def normalize_title(title: str) -> str:
    """Привести заголовок к каноничному виду для dedup: lowercase, без
    пунктуации, схлопнутые пробелы."""
    t = title.casefold().strip()
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t


def dedup_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


# --------- items ---------

@dataclass
class Item:
    source: str          # 'rss:<feed-host>' | 'reddit:<sub>'
    title: str
    url: str | None
    payload: dict


def _classify_source(feed_url: str) -> tuple[str, str]:
    """('reddit:<sub>', sub) для reddit-URL; ('rss:<host>', host) для остальных."""
    p = urlparse(feed_url)
    host = p.hostname or feed_url
    # old.reddit.com и www.reddit.com — оба «reddit», нормализуем.
    if "reddit.com" in host:
        # /r/<sub>/.rss → sub
        parts = [s for s in p.path.split("/") if s]
        if len(parts) >= 2 and parts[0] == "r":
            sub = parts[1]
            return f"reddit:{sub}", sub
    return f"rss:{host}", host


REDDIT_RATE_DELAY_SEC = 5.0          # базовая пауза между Reddit-запросами
_REDDIT_RETRY_DELAYS = (5.0, 15.0, 30.0, 60.0)


def _http_get_with_backoff(url: str, *, is_reddit: bool) -> str | None:
    """GET с управляемым backoff'ом. Для Reddit на 429 — экспоненциальный
    backoff 5/15/30/60с с jitter. Reddit жёстко режет VPS IP — это
    необходимый компромисс между coverage и временем прогона."""
    import random

    import httpx
    last_status: int | str = "?"
    attempts = len(_REDDIT_RETRY_DELAYS) if is_reddit else 1
    for attempt in range(attempts):
        try:
            r = httpx.get(url, headers={"User-Agent": _USER_AGENT},
                          timeout=20.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            log.warning("rss http error %s: %s", url, exc)
            return None
        if r.status_code == 200:
            return r.text
        last_status = r.status_code
        if is_reddit and r.status_code == 429 and attempt < attempts - 1:
            delay = _REDDIT_RETRY_DELAYS[attempt] + random.uniform(0, 2)
            log.info("reddit 429 on %s, backoff %.1fs (attempt %d/%d)",
                     url, delay, attempt + 1, attempts)
            time.sleep(delay)
            continue
        break
    log.warning("rss %s status=%s", url, last_status)
    return None


def fetch_rss(feeds: Iterable[str] = RSS_FEEDS,
              per_feed: int = RSS_PER_FEED,
              reddit_delay_sec: float = REDDIT_RATE_DELAY_SEC) -> list[Item]:
    """Парсит все RSS-фиды (включая Reddit-сабы) одним проходом.

    Reddit rate-limit'ит rapid sequential `*.reddit.com/.rss` запросы (429
    после второго). HTTP-запрос выносим в свой httpx-цикл с backoff'ом на
    429; обычные news-ленты идут без задержки.
    """
    try:
        import feedparser  # type: ignore
    except ImportError:
        log.error("feedparser not installed")
        return []

    # Перемешиваем — Reddit-сабы получают «приоритет дня» по-разному при
    # каждом прогоне, что выравнивает coverage поверх жёсткого rate-limit'а.
    import random as _random
    feed_list = list(feeds)
    _random.shuffle(feed_list)

    out: list[Item] = []
    last_reddit_at = 0.0
    for url in feed_list:
        source, where = _classify_source(url)
        is_reddit = source.startswith("reddit:")
        if is_reddit and reddit_delay_sec > 0:
            wait = reddit_delay_sec - (time.monotonic() - last_reddit_at)
            if wait > 0:
                time.sleep(wait)
        text = _http_get_with_backoff(url, is_reddit=is_reddit)
        if is_reddit:
            last_reddit_at = time.monotonic()
        if not text:
            continue
        try:
            d = feedparser.parse(text)
        except Exception:
            log.warning("rss parse failed: %s", url, exc_info=True)
            continue
        if getattr(d, "bozo", 0) and not (d.entries or []):
            log.warning("rss empty/malformed: %s (bozo=%s)", url,
                        getattr(d, "bozo_exception", "?"))
            continue
        for entry in (d.entries or [])[:per_feed]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            link = entry.get("link")
            payload: dict = {
                "summary": (entry.get("summary") or "")[:1000],
                "published": entry.get("published") or entry.get("updated"),
            }
            if is_reddit:
                payload["subreddit"] = where
                # Reddit RSS не отдаёт upvote count; в `hot` ленте посты
                # обычно >100 upvotes по алгоритму Reddit — Strategist
                # узнаёт это через source-префикс reddit:<sub>.
                payload["source_kind"] = "reddit_hot"
            out.append(Item(source=source, title=title, url=link, payload=payload))
    return out


# --------- persistence ---------

def insert_items(items: Iterable[Item]) -> tuple[int, int]:
    """Возвращает (inserted, skipped). ON CONFLICT(dedup_hash) DO NOTHING."""
    sql = (
        "INSERT INTO topics (source, title, url, raw_payload, dedup_hash) "
        "VALUES (%s, %s, %s, %s::jsonb, %s) "
        "ON CONFLICT (dedup_hash) DO NOTHING "
        "RETURNING id"
    )
    inserted = total = 0
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for it in items:
                    total += 1
                    cur.execute(sql, (
                        it.source, it.title, it.url,
                        json.dumps(it.payload, default=str),
                        dedup_hash(it.title),
                    ))
                    if cur.fetchone():
                        inserted += 1
    return inserted, total - inserted


def run_once() -> int:
    t0 = time.monotonic()
    items = fetch_rss()
    n_reddit = sum(1 for it in items if it.source.startswith("reddit:"))
    n_news = len(items) - n_reddit
    inserted, skipped = insert_items(items)
    log.info(
        "trend_scout: news=%d reddit=%d → inserted=%d skipped=%d (%.1fs)",
        n_news, n_reddit, inserted, skipped, time.monotonic() - t0,
    )
    return inserted


__all__ = [
    "WORKER_NAME",
    "normalize_title", "dedup_hash",
    "Item", "fetch_rss", "insert_items", "run_once",
]
