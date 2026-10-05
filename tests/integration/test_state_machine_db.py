"""Интеграционные тесты state machine: SKIP LOCKED + status_log.

Запускается, только если задан TEST_DB_URL (чистая тестовая Postgres,
к которой применены миграции из ./migrations).
"""

from __future__ import annotations

import os
import threading
import uuid

import psycopg
import pytest

TEST_DB_URL = os.getenv("TEST_DB_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DB_URL not set — integration test skipped",
)


@pytest.fixture
def conn_factory():
    def make():
        return psycopg.connect(TEST_DB_URL, autocommit=False)
    return make


@pytest.fixture
def video_id(conn_factory):
    vid = str(uuid.uuid4())
    with conn_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO videos (id, format, status) VALUES (%s, 'short', 'voiced')",
                (vid,),
            )
        conn.commit()
    yield vid
    with conn_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM status_log WHERE video_id = %s", (vid,))
            cur.execute("DELETE FROM videos WHERE id = %s", (vid,))
        conn.commit()


def test_skip_locked_yields_record_once(conn_factory, video_id, monkeypatch):
    """Два «воркера» одновременно дёргают claim('voiced') — запись достаётся ровно одному."""
    from src.core import db as db_mod
    from src.core.state_machine import StateMachine

    db_mod.set_connect_override(conn_factory)
    try:
        results: list[list] = [[], []]
        barrier = threading.Barrier(2)

        def worker(i: int) -> None:
            barrier.wait()
            sm = StateMachine(f"w{i}")
            results[i] = sm.claim("voiced", limit=1)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        captured = sum(len(r) for r in results)
        assert captured == 1, f"expected exactly one worker to claim, got {captured}"
    finally:
        db_mod.set_connect_override(None)


def test_transition_writes_status_log(conn_factory, video_id):
    from src.core import db as db_mod
    from src.core.state_machine import StateMachine

    db_mod.set_connect_override(conn_factory)
    try:
        sm = StateMachine("test")
        sm.transition(video_id, "voiced", "assembled", detail="ok")
        with conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM videos WHERE id = %s", (video_id,))
                assert cur.fetchone()[0] == "assembled"
                cur.execute(
                    "SELECT actor, from_status, to_status FROM status_log "
                    "WHERE video_id = %s ORDER BY id DESC LIMIT 1",
                    (video_id,),
                )
                row = cur.fetchone()
                assert row == ("test", "voiced", "assembled")
    finally:
        db_mod.set_connect_override(None)


def test_transition_video_not_found(conn_factory):
    """missing video → VideoNotFound."""
    import uuid as _uuid
    from src.core import db as db_mod
    from src.core.state_machine import StateMachine, VideoNotFound

    db_mod.set_connect_override(conn_factory)
    try:
        sm = StateMachine("test")
        with pytest.raises(VideoNotFound):
            sm.transition(str(_uuid.uuid4()), "raw", "scored")
    finally:
        db_mod.set_connect_override(None)


def test_transition_status_mismatch(conn_factory, video_id):
    """текущий status != from_status → InvalidTransition с актуальным значением."""
    from src.core import db as db_mod
    from src.core.state_machine import InvalidTransition, StateMachine

    db_mod.set_connect_override(conn_factory)
    try:
        sm = StateMachine("test")
        # видео в 'voiced', мы говорим что было 'approved'
        with pytest.raises(InvalidTransition) as ei:
            sm.transition(video_id, "approved", "scripted")
        assert ei.value.from_status == "voiced"
    finally:
        db_mod.set_connect_override(None)


def test_transition_with_last_error_and_bump_attempts(conn_factory, video_id):
    """fields list строится из last_error + bump_attempts."""
    from src.core import db as db_mod
    from src.core.state_machine import StateMachine

    db_mod.set_connect_override(conn_factory)
    try:
        sm = StateMachine("test")
        sm.transition(
            video_id, "voiced", "failed",
            last_error="explicit fail", bump_attempts=True,
        )
        with conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status::text, last_error, attempts "
                    "FROM videos WHERE id = %s",
                    (video_id,),
                )
                status, err, attempts = cur.fetchone()
        assert status == "failed"
        assert err == "explicit fail"
        assert attempts == 1
    finally:
        db_mod.set_connect_override(None)
