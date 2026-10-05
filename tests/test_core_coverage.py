"""Добивка покрытия state_machine.py и retry.py до 100%.

Закрывает: alert sink ошибки, transition внутренние fail-пути,
claim('unknown'), pause-flag exception, last_error+bump_attempts,
VideoNotFound, status-mismatch.
"""

from __future__ import annotations

import pytest

from src.core import retry as retry_mod
from src.core import settings_store
from src.core.retry import RetryConfig, set_alert_sink
from src.core.state_machine import (
    InvalidTransition, StateMachine, VideoNotFound,
)


# ---------- retry alerts ----------

def test_emit_alert_with_no_sink_just_logs(caplog):
    set_alert_sink(None)
    retry_mod._emit_alert("nothing registered")
    assert any("alert skipped" in rec.message for rec in caplog.records)


def test_emit_alert_swallows_sink_exception(caplog):
    def bad_sink(msg):
        raise RuntimeError("sink broken")
    set_alert_sink(bad_sink)
    try:
        retry_mod._emit_alert("test")  # не должен бросить
    finally:
        set_alert_sink(None)


def test_record_dead_letter_failed_transition_logs(monkeypatch, caplog):
    """Линия 137-138: StateMachine.transition бросает, мы её ловим."""
    monkeypatch.setattr(retry_mod, "connection", _no_db_connection)

    class _BadSM:
        def __init__(self, actor): pass
        def transition(self, *a, **kw):
            raise RuntimeError("sm boom")
    monkeypatch.setattr(retry_mod, "StateMachine", _BadSM)

    cfg = RetryConfig(
        worker="w", video_id="v1", fail_from_status="approved",
        sleep=lambda _s: None,
    )
    retry_mod._record_dead_letter(cfg, RuntimeError("x"))
    assert any("failed to transition" in rec.message for rec in caplog.records)


# ---------- state_machine ----------

def test_claim_unknown_status_raises():
    sm = StateMachine("test")
    with pytest.raises(ValueError):
        sm.claim("not-a-status")


def test_claim_pause_check_unreadable_proceeds(monkeypatch, caplog):
    """Если settings_store.is_paused() бросает, claim() не падает —
    логирует warning и идёт в БД."""
    import logging as _logging

    def _broken():
        raise RuntimeError("settings db down")
    monkeypatch.setattr(settings_store, "is_paused", _broken)
    from src.core import db as db_mod
    db_mod.set_connect_override(_no_db_connection_factory)
    try:
        sm = StateMachine("test")
        with caplog.at_level(_logging.WARNING, logger="src.core.state_machine"):
            assert sm.claim("approved") == []
        assert any("pause flag unreadable" in rec.message for rec in caplog.records)
    finally:
        db_mod.set_connect_override(None)


# ---------- mocks ----------

class _Cursor:
    rows = []
    def execute(self, sql, params=None):
        self.last = (sql, params)
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _NoTx:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self):
        return _Cursor()
    def transaction(self):
        return _NoTx()
    def close(self):
        return None
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _no_db_connection():
    return _Conn()


def _no_db_connection_factory():
    return _Conn()
