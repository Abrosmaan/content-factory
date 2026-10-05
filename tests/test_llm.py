"""Юнит-тесты llm wrapper: счётчики токенов, кеш-обёртка, JSON retry, non-retryable."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.core import llm, retry as retry_mod, settings_store


# ---------------- fake Anthropic client ----------------

class FakeUsage:
    def __init__(self, *, in_t=10, out_t=5, cache_read=0, cache_create=0):
        self.input_tokens = in_t
        self.output_tokens = out_t
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_create


class FakeMessage:
    def __init__(self, text: str, usage: FakeUsage):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = usage


class FakeMessages:
    def __init__(self, queue: list[Any]):
        self.queue = queue
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, queue: list[Any]):
        self.messages = FakeMessages(queue)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Сброс lru_cache клиента + стаб settings_store.increment."""
    llm._client.cache_clear()
    increments: list[tuple[str, int]] = []
    monkeypatch.setattr(
        settings_store,
        "increment",
        lambda key, by=1: increments.append((key, by)) or 0,
    )
    yield increments


def _inject(monkeypatch, queue: list[Any]) -> FakeClient:
    client = FakeClient(queue)
    monkeypatch.setattr(llm, "_client", lambda: client)
    return client


# ---------------- complete ----------------

def test_complete_returns_text_and_counts_tokens(monkeypatch, reset_state):
    client = _inject(monkeypatch, [FakeMessage("hello world", FakeUsage(in_t=12, out_t=7))])
    out = llm.complete(
        [{"role": "user", "content": "hi"}],
        system="you are helpful",
        max_tokens=128,
    )
    assert out == "hello world"
    # один вызов, system обёрнут в список с cache_control
    assert len(client.messages.calls) == 1
    sys_arg = client.messages.calls[0]["system"]
    assert isinstance(sys_arg, list) and sys_arg[0]["cache_control"] == {"type": "ephemeral"}
    # счётчики
    assert ("tokens_claude_in", 12) in reset_state
    assert ("tokens_claude_out", 7) in reset_state


def test_complete_accumulates_cache_tokens(monkeypatch, reset_state):
    usage = FakeUsage(in_t=4, out_t=3, cache_read=100, cache_create=8)
    _inject(monkeypatch, [FakeMessage("ok", usage)])
    llm.complete([{"role": "user", "content": "x"}])
    # tokens_in суммирует input + cache_read + cache_create
    assert ("tokens_claude_in", 112) in reset_state
    assert ("tokens_claude_out", 3) in reset_state


def test_complete_passes_through_list_system(monkeypatch, reset_state):
    client = _inject(monkeypatch, [FakeMessage("ok", FakeUsage())])
    blocks = [{"type": "text", "text": "sys1"}, {"type": "text", "text": "sys2",
              "cache_control": {"type": "ephemeral"}}]
    llm.complete([{"role": "user", "content": "x"}], system=blocks)
    assert client.messages.calls[0]["system"] == blocks


def test_complete_omits_empty_system(monkeypatch, reset_state):
    client = _inject(monkeypatch, [FakeMessage("ok", FakeUsage())])
    llm.complete([{"role": "user", "content": "x"}], system="   ")
    assert "system" not in client.messages.calls[0]


def test_complete_requires_non_empty_messages(monkeypatch, reset_state):
    _inject(monkeypatch, [])
    with pytest.raises(ValueError):
        llm.complete([])


def test_complete_uses_custom_model(monkeypatch, reset_state):
    client = _inject(monkeypatch, [FakeMessage("ok", FakeUsage())])
    llm.complete([{"role": "user", "content": "x"}], model="claude-haiku-4-5")
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5"


def test_default_model_is_sonnet_4_5(monkeypatch, reset_state):
    client = _inject(monkeypatch, [FakeMessage("ok", FakeUsage())])
    llm.complete([{"role": "user", "content": "x"}])
    assert client.messages.calls[0]["model"] == "claude-sonnet-4-5"


# ---------------- retry behaviour ----------------

def test_complete_retries_transient(monkeypatch, reset_state):
    sleeps: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(retry_mod, "_record_dead_letter", lambda cfg, exc: None)
    _inject(monkeypatch, [
        RuntimeError("transient1"),
        RuntimeError("transient2"),
        FakeMessage("recovered", FakeUsage(in_t=2, out_t=1)),
    ])
    out = llm.complete([{"role": "user", "content": "x"}])
    assert out == "recovered"
    assert len(sleeps) == 2  # 2 retry'я → 2 sleep


def test_complete_does_not_retry_bad_request(monkeypatch, reset_state):
    import anthropic
    sleeps: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(retry_mod, "_record_dead_letter", lambda cfg, exc: None)

    class _Resp:
        status_code = 400
        request = None
        headers: dict = {}
    err = anthropic.BadRequestError("bad", response=_Resp(), body={"error": {"message": "bad"}})
    _inject(monkeypatch, [err, err, err])
    with pytest.raises(retry_mod.DeadLetter):
        llm.complete([{"role": "user", "content": "x"}])
    # non_retryable → ни одного sleep
    assert sleeps == []


# ---------------- complete_json ----------------

def test_complete_json_parses_first_try(monkeypatch, reset_state):
    _inject(monkeypatch, [FakeMessage('{"a": 1, "b": [2, 3]}', FakeUsage())])
    obj = llm.complete_json([{"role": "user", "content": "give json"}])
    assert obj == {"a": 1, "b": [2, 3]}


def test_complete_json_strips_code_fence(monkeypatch, reset_state):
    fenced = "Here is the JSON:\n```json\n{\"x\": 42}\n```"
    _inject(monkeypatch, [FakeMessage(fenced, FakeUsage())])
    assert llm.complete_json([{"role": "user", "content": "x"}]) == {"x": 42}


def test_complete_json_retries_once_on_bad_json(monkeypatch, reset_state):
    client = _inject(monkeypatch, [
        FakeMessage("not a json {oops", FakeUsage(in_t=3, out_t=2)),
        FakeMessage('{"ok": true}', FakeUsage(in_t=4, out_t=1)),
    ])
    obj = llm.complete_json([{"role": "user", "content": "x"}])
    assert obj == {"ok": True}
    # второй запрос содержит assistant ответ и corrective user message
    second = client.messages.calls[1]["messages"]
    roles = [m["role"] for m in second]
    assert roles == ["user", "assistant", "user"]
    assert "not valid JSON" in second[-1]["content"]


def test_complete_json_propagates_after_second_failure(monkeypatch, reset_state):
    _inject(monkeypatch, [
        FakeMessage("garbage1", FakeUsage()),
        FakeMessage("garbage2", FakeUsage()),
    ])
    import json as _json
    with pytest.raises(_json.JSONDecodeError):
        llm.complete_json([{"role": "user", "content": "x"}])
