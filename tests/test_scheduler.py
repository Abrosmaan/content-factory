"""Юнит-тесты scheduler.setup + pause-флаг."""

from __future__ import annotations

import asyncio

import pytest

from src import scheduler
from src.core import settings_store


def test_setup_registers_all_jobs():
    s = scheduler.setup_scheduler()
    ids = {j.id for j in s.get_jobs()}
    assert "trend_scout" in ids
    assert "strategist" in ids
    assert "digest" in ids
    for w in ("scriptwriter", "voice", "assembler",
              "thumbnail", "seo_packager", "publisher"):
        assert f"poll_{w}" in ids
    # очистить чтобы следующий тест получал чистый scheduler
    s.remove_all_jobs()


def test_safe_call_skips_when_paused(monkeypatch):
    monkeypatch.setattr(settings_store, "is_paused", lambda: True)
    calls = {"n": 0}
    def fake():
        calls["n"] += 1
    coro_factory = scheduler._safe_call(fake, name="test")
    asyncio.get_event_loop().run_until_complete(coro_factory())
    assert calls["n"] == 0, "fn must NOT be called when paused"


def test_safe_call_runs_when_unpaused(monkeypatch):
    monkeypatch.setattr(settings_store, "is_paused", lambda: False)
    calls = {"n": 0}
    def fake():
        calls["n"] += 1
    asyncio.get_event_loop().run_until_complete(
        scheduler._safe_call(fake, name="test")()
    )
    assert calls["n"] == 1


def test_safe_call_swallows_exceptions(monkeypatch):
    monkeypatch.setattr(settings_store, "is_paused", lambda: False)
    def boom():
        raise RuntimeError("nope")
    # не должен бросить
    asyncio.get_event_loop().run_until_complete(
        scheduler._safe_call(boom, name="boom")()
    )
