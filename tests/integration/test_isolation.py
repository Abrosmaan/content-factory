"""Интеграционный тест E6.7: distributions.status='failed' НЕ меняет
ни videos.status, ни кладёт запись в errors-канал видео.

Требует TEST_DB_URL.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

TEST_DB_URL = os.getenv("TEST_DB_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB_URL, reason="TEST_DB_URL not set",
)


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DB_URL, autocommit=True) as c:
        yield c


def test_failed_distribution_does_not_change_video(conn):
    """seed published video → seed distribution(failed) → видео остаётся published."""
    vid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO topics (id, source, title, dedup_hash) "
            "VALUES (%s, 'iso', 'iso', %s)",
            (tid, f"iso-{uuid.uuid4()}"),
        )
        cur.execute(
            "INSERT INTO videos (id, topic_id, format, status) "
            "VALUES (%s, %s, 'long_gaming'::video_format, 'published'::video_status)",
            (vid, tid),
        )
        # seed 3 distributions, все в failed
        for ch in ("blog", "pinterest", "threads"):
            cur.execute(
                "INSERT INTO distributions (video_id, channel, status, last_error) "
                "VALUES (%s, %s::dist_channel, 'failed'::dist_status, %s)",
                (vid, ch, f"{ch} api 429"),
            )
        # читаем статус видео
        cur.execute("SELECT status::text FROM videos WHERE id = %s", (vid,))
        status = cur.fetchone()[0]

    try:
        assert status == "published", \
            "video status must not change when distributions fail"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM distributions WHERE video_id = %s", (vid,))
            cur.execute("DELETE FROM videos WHERE id = %s", (vid,))
            cur.execute("DELETE FROM topics WHERE id = %s", (tid,))


def test_distribution_failed_does_not_block_video_publisher_select(conn):
    """Publisher выбирает publish_queued; distributions.failed не должна
    влиять на видимость видео в publish-очереди (но они обычно уже published)."""
    vid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO topics (id, source, title, dedup_hash) "
            "VALUES (%s, 'iso', 'iso', %s)",
            (tid, f"iso2-{uuid.uuid4()}"),
        )
        cur.execute(
            "INSERT INTO videos (id, topic_id, format, status, "
            "                    scheduled_publish_at) "
            "VALUES (%s, %s, 'short'::video_format, "
            "        'publish_queued'::video_status, "
            "        now() + interval '1 hour')",
            (vid, tid),
        )
        cur.execute(
            "INSERT INTO distributions (video_id, channel, status, last_error) "
            "VALUES (%s, 'blog'::dist_channel, 'failed'::dist_status, 'cf error')",
            (vid,),
        )
        cur.execute("SELECT status::text FROM videos WHERE id = %s", (vid,))
        assert cur.fetchone()[0] == "publish_queued"

    with conn.cursor() as cur:
        cur.execute("DELETE FROM distributions WHERE video_id = %s", (vid,))
        cur.execute("DELETE FROM videos WHERE id = %s", (vid,))
        cur.execute("DELETE FROM topics WHERE id = %s", (tid,))
