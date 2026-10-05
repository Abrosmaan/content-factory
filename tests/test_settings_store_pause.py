"""Тест: StateMachine.claim уважает /pause-флаг."""

from __future__ import annotations

from src.core import settings_store
from src.core.state_machine import StateMachine


def test_claim_skips_when_paused(monkeypatch):
    """is_paused() == True → claim() возвращает пустой список без запроса в БД."""
    monkeypatch.setattr(settings_store, "is_paused", lambda: True)

    called = {"db": 0}
    def fake_conn():
        called["db"] += 1
        raise AssertionError("DB shouldn't be touched when paused")

    from src.core import db as db_mod
    db_mod.set_connect_override(fake_conn)
    try:
        sm = StateMachine("test")
        assert sm.claim("approved") == []
        assert called["db"] == 0
    finally:
        db_mod.set_connect_override(None)
