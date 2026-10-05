"""Юнит-тесты retry: счётчики попыток, последовательность задержек, dead-letter."""

from __future__ import annotations

import pytest

from src.core import retry as retry_mod
from src.core.retry import (
    DEFAULT_DELAYS,
    DEFAULT_MAX_ATTEMPTS,
    DeadLetter,
    RetryConfig,
    run_with_retry,
    set_alert_sink,
)


@pytest.fixture
def fake_sleep():
    """Подменяет sleep, чтобы тесты не висели реальные секунды."""
    calls: list[float] = []
    def _sleep(s: float) -> None:
        calls.append(s)
    return calls, _sleep


@pytest.fixture
def stub_record(monkeypatch):
    """Подменяет _record_dead_letter — без сети/БД."""
    records: list[tuple[RetryConfig, BaseException]] = []
    def _rec(cfg: RetryConfig, exc: BaseException) -> None:
        records.append((cfg, exc))
    monkeypatch.setattr(retry_mod, "_record_dead_letter", _rec)
    return records


def test_succeeds_first_attempt(fake_sleep, stub_record):
    _, sleep = fake_sleep
    cfg = RetryConfig(worker="w", video_id="v", sleep=sleep)
    assert run_with_retry(lambda: 42, cfg) == 42
    assert stub_record == []


def test_succeeds_after_two_failures(fake_sleep, stub_record):
    sleep_calls, sleep = fake_sleep
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"boom-{calls['n']}")
        return "ok"
    cfg = RetryConfig(worker="w", video_id="v", sleep=sleep)
    assert run_with_retry(fn, cfg) == "ok"
    assert calls["n"] == 3
    assert sleep_calls == [DEFAULT_DELAYS[0], DEFAULT_DELAYS[1]]
    assert stub_record == []


def test_dead_letter_after_three_failures(fake_sleep, stub_record):
    sleep_calls, sleep = fake_sleep
    cfg = RetryConfig(worker="voice", video_id="v123", sleep=sleep)
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise RuntimeError(f"boom-{calls['n']}")
    with pytest.raises(DeadLetter) as ei:
        run_with_retry(fn, cfg)
    assert calls["n"] == DEFAULT_MAX_ATTEMPTS == 3
    assert sleep_calls == [DEFAULT_DELAYS[0], DEFAULT_DELAYS[1]]
    assert len(stub_record) == 1
    rec_cfg, rec_exc = stub_record[0]
    assert rec_cfg.worker == "voice"
    assert rec_cfg.video_id == "v123"
    assert isinstance(rec_exc, RuntimeError)
    assert ei.value.worker == "voice"
    assert ei.value.video_id == "v123"


def test_alert_sink_called_on_dead_letter(monkeypatch):
    """Алерт-хук получает читаемое сообщение при dead-letter."""
    # Не мокаем _record_dead_letter полностью — мокаем только БД-часть.
    captured: list[str] = []
    set_alert_sink(lambda msg: captured.append(msg))
    monkeypatch.setattr(retry_mod, "connection", _no_db_connection)
    monkeypatch.setattr(
        retry_mod,
        "StateMachine",
        lambda actor: _StubSM(actor),
    )
    try:
        cfg = RetryConfig(
            worker="scriptwriter",
            video_id="vid-X",
            sleep=lambda _s: None,
            fail_from_status="approved",
        )
        with pytest.raises(DeadLetter):
            run_with_retry(lambda: (_ for _ in ()).throw(ValueError("oops")), cfg)
    finally:
        set_alert_sink(None)
    assert captured, "alert sink не получил сообщение"
    msg = captured[0]
    assert "scriptwriter" in msg
    assert "vid-X" in msg
    assert "ValueError" in msg


def test_custom_max_and_delays(stub_record):
    sleeps: list[float] = []
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        raise RuntimeError("x")
    cfg = RetryConfig(
        worker="w",
        video_id="v",
        max_attempts=2,
        delays=(1, 2),
        sleep=sleeps.append,
    )
    with pytest.raises(DeadLetter):
        run_with_retry(fn, cfg)
    assert calls["n"] == 2
    assert sleeps == [1]


# ---------- helpers ----------

class _StubSM:
    def __init__(self, actor: str) -> None:
        self.actor = actor
    def transition(self, *args, **kwargs):
        # noop: state-machine движение в БД-моке не нужно
        return None


class _Cursor:
    def __init__(self):
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return None
    def fetchall(self):
        return []
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _Conn:
    def cursor(self):
        return _Cursor()
    def transaction(self):
        return _NoCtx()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _NoCtx:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _no_db_connection():
    return _Conn()
