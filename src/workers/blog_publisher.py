"""Blog Publisher (спецификация 6.12 ТЗ).

Поллит distributions(channel='blog', status='content_ready') →
1. пишет markdown в blog/src/content/posts/<slug>.md (frontmatter:
   title, date, video_id, youtube_url, products[])
2. вызывает astro build + wrangler pages deploy --project-name=<name>
3. ждёт пока URL отвечает 200 → status='posted', external_url.

[!] Smoke на проде требует CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID /
BLOG_DOMAIN. Без них git status и логика парсинга content корректна.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from src.core.config import get_settings, project_root, require
from src.core.db import connection

log = logging.getLogger(__name__)

WORKER_NAME = "blog_publisher"
BLOG_DIR = project_root() / "blog"
POSTS_DIR = BLOG_DIR / "src" / "content" / "posts"


def _claim_content_ready() -> dict | None:
    sql = (
        "SELECT d.id::text, d.video_id::text, d.content, v.title, "
        "       v.youtube_video_id, v.seo "
        "FROM distributions d JOIN videos v ON v.id = d.video_id "
        "WHERE d.channel = 'blog' AND d.status = 'content_ready' "
        "ORDER BY d.id "
        "FOR UPDATE OF d SKIP LOCKED LIMIT 1"
    )
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "video_id": row[1], "content": row[2] or {},
        "title": row[3], "youtube_id": row[4], "seo": row[5] or {},
    }


def _mark_posted(conn, dist_id: str, external_url: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE distributions SET status = 'posted', external_url = %s, "
            "posted_at = now() WHERE id = %s",
            (external_url, dist_id),
        )


def _mark_failed(conn, dist_id: str, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE distributions SET status = 'failed', last_error = %s, "
            "attempts = attempts + 1 WHERE id = %s",
            (error, dist_id),
        )


def build_frontmatter(*, title: str, date: str, video_id: str,
                      youtube_id: str | None,
                      products: list[dict[str, Any]]) -> str:
    fm: dict[str, Any] = {
        "title": title,
        "date": date,
        "video_id": video_id,
        "products": [
            {"name": p.get("name", ""), "url": p.get("url", ""),
             "price_usd": p.get("price_usd")}
            for p in (products or [])
            if isinstance(p, dict) and p.get("url")
        ],
    }
    if youtube_id:
        fm["youtube_url"] = f"https://www.youtube.com/watch?v={youtube_id}"
    return "---\n" + json.dumps(fm, indent=2, ensure_ascii=False) + "\n---\n"


def write_post(slug: str, frontmatter: str, body_md: str) -> Path:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    path = POSTS_DIR / f"{slug}.md"
    path.write_text(frontmatter + "\n" + body_md.strip() + "\n",
                    encoding="utf-8")
    return path


def deploy() -> str:
    """Astro build + wrangler pages deploy. Возвращает URL."""
    s = require("epic6")
    project = (s.BLOG_DOMAIN or "content-factory-blog").split(".")[0]
    subprocess.check_call(["npm", "run", "build"], cwd=str(BLOG_DIR))
    out = subprocess.check_output(
        ["npx", "wrangler", "pages", "deploy", "dist",
         f"--project-name={project}", "--branch=main"],
        cwd=str(BLOG_DIR), env={
            "CLOUDFLARE_API_TOKEN": s.CLOUDFLARE_API_TOKEN,
            "CLOUDFLARE_ACCOUNT_ID": s.CLOUDFLARE_ACCOUNT_ID,
            **__import__("os").environ,
        },
        text=True,
    )
    return f"https://{s.BLOG_DOMAIN}" if s.BLOG_DOMAIN else "https://localhost"


def verify(url: str, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=10.0, follow_redirects=True)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(3)
    return False


def process_one(rec: dict) -> str:
    require("epic6")
    content = rec["content"] or {}
    slug = (content.get("slug") or rec["video_id"]).lower()
    blog_title = content.get("title") or rec["title"] or "Untitled"
    markdown = content.get("markdown") or ""
    seo = rec["seo"] or {}
    products = seo.get("products") or []

    import datetime as dt
    date_iso = dt.date.today().isoformat()
    fm = build_frontmatter(
        title=blog_title, date=date_iso, video_id=rec["video_id"],
        youtube_id=rec.get("youtube_id"), products=products,
    )
    write_post(slug, fm, markdown)
    try:
        url = deploy()
    except subprocess.CalledProcessError as exc:
        log.exception("deploy failed")
        with connection() as conn:
            with conn.transaction():
                _mark_failed(conn, rec["id"], f"deploy: {exc}")
        return "failed"

    post_url = f"{url.rstrip('/')}/posts/{slug}/"
    if not verify(post_url):
        with connection() as conn:
            with conn.transaction():
                _mark_failed(conn, rec["id"], f"verify 200 timeout: {post_url}")
        return "failed"

    with connection() as conn:
        with conn.transaction():
            _mark_posted(conn, rec["id"], post_url)
    return "posted"


def run_once() -> int:
    # Единый kill-switch для всех дистрибуционных каналов.
    from src.core import settings_store
    if settings_store.is_distribution_paused():
        return 0
    rec = _claim_content_ready()
    if not rec:
        return 0
    try:
        process_one(rec)
        return 1
    except Exception:
        log.exception("blog publisher failed")
        return 0


__all__ = [
    "WORKER_NAME",
    "build_frontmatter",
    "write_post",
    "deploy",
    "verify",
    "process_one",
    "run_once",
]
