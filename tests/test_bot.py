"""Бот: чистые функции форматирования и send_alert через HTTP-мок."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.bot.alerts import send_alert
from src.bot.handlers import format_status_text


def test_format_status_empty():
    text = format_status_text({}, [])
    assert "no videos yet" in text


def test_format_status_counts_and_errors():
    counts = {"raw": 3, "approved": 1}
    errors = [("voice", "elevenlabs 429"), ("assembler", "footage_gap: cs2 menu")]
    text = format_status_text(counts, errors)
    assert "`raw`: 3" in text
    assert "`approved`: 1" in text
    assert "voice" in text
    assert "elevenlabs 429" in text


def test_format_status_truncates_errors():
    """В сводке /status показываются только последние 5 ошибок."""
    errors = [(f"w{i}", f"err{i}") for i in range(10)]
    text = format_status_text({"raw": 0}, errors)
    assert "w0" in text and "w4" in text
    assert "w5" not in text


@respx.mock
def test_send_alert_posts_to_telegram(monkeypatch):
    # подсовываем переменные окружения, чтобы get_settings подхватил
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "555")
    # сбросить закешированный экземпляр настроек (BaseSettings читает env при инстанцировании)
    route = respx.post("https://api.telegram.org/botTESTTOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    send_alert("hello")
    assert route.called
    body = route.calls[0].request.content.decode()
    assert "555" in body
    assert "hello" in body


@respx.mock
def test_send_alert_swallows_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "555")
    respx.post("https://api.telegram.org/botTESTTOKEN/sendMessage").mock(
        side_effect=httpx.ConnectError("boom")
    )
    # не должен ронять воркер
    send_alert("oops")


def test_send_alert_noop_without_creds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_OWNER_CHAT_ID", "")
    # просто не падать (никаких HTTP-запросов)
    send_alert("nothing")
