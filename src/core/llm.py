"""Anthropic API wrapper.

Один синхронный entry для всех воркеров: `complete(messages, system, ...)`
+ `complete_json(...)` для тех мест, где нужен валидный JSON ответ.

ТЗ-контракт:
  • Model по умолчанию — `claude-sonnet-4-5` (раздел 3 ТЗ, фикс).
  • Все вызовы через retry.run_with_retry (3 попытки, backoff 5/25/125).
    Auth/Permission/BadRequest не ретраятся (retry не лечит).
  • Счётчики токенов в `settings`:
        tokens_claude_in  = input + cache_read + cache_creation
        tokens_claude_out = output
  • Prompt caching: длинные system-промпты Scriptwriter'а / SEO / Repurposer
    переиспользуются между запросами → если system пришёл строкой, оборачиваем
    в один блок с `cache_control: ephemeral`. Если уже список — отдаём как есть.

complete_json делает один corrective retry если ответ не JSON.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

import anthropic

from src.core import costs, settings_store
from src.core.config import require
from src.core.retry import RetryConfig, run_with_retry

log = logging.getLogger(__name__)

# Фиксированная модель из раздела 3 ТЗ. Подмена в коде запрещена.
DEFAULT_MODEL = "claude-sonnet-4-5"

# Не ретраимые ошибки Anthropic: retry не починит.
_NON_RETRYABLE: tuple[type[BaseException], ...] = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.BadRequestError,
    anthropic.NotFoundError,
)


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    s = require("epic1")  # epic1 уже содержит ANTHROPIC_API_KEY
    return anthropic.Anthropic(api_key=s.ANTHROPIC_API_KEY)


def _coerce_system(system: str | list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Строку оборачиваем в один блок с ephemeral cache_control (большие prompt'ы
    в Scriptwriter/SEO/Repurposer переиспользуются между запросами).

    Список оставляем как есть — caller знает, что делает.
    """
    if system is None:
        return None
    if isinstance(system, str):
        if not system.strip():
            return None
        return [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]
    return system


def _extract_text(message: anthropic.types.Message) -> str:
    parts: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def _account_tokens(usage: Any, *, worker: str | None = None,
                    video_id: str | None = None) -> None:
    """Увеличить счётчики settings.tokens_claude_in / _out + costs."""
    if usage is None:
        return
    in_tokens = (
        (getattr(usage, "input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    out_tokens = getattr(usage, "output_tokens", 0) or 0
    if in_tokens:
        try:
            settings_store.increment("tokens_claude_in", in_tokens)
        except Exception:  # БД может быть ненадолго недоступна — не ломаем вызов
            log.warning("token accounting failed (in=%d)", in_tokens, exc_info=True)
    if out_tokens:
        try:
            settings_store.increment("tokens_claude_out", out_tokens)
        except Exception:
            log.warning("token accounting failed (out=%d)", out_tokens, exc_info=True)
    if in_tokens or out_tokens:
        costs.record_anthropic(in_tokens, out_tokens, worker=worker,
                               video_id=video_id)


def _call(
    *,
    model: str,
    system: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    max_tokens: int,
    extra: dict[str, Any] | None = None,
) -> anthropic.types.Message:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system is not None:
        kwargs["system"] = system
    if extra:
        kwargs.update(extra)
    return _client().messages.create(**kwargs)


def complete(
    messages: list[dict[str, Any]],
    *,
    system: str | list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    model: str | None = None,
    worker: str = "llm",
    video_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Один LLM-запрос. Возвращает текст ответа.

    `system` — строка (будет обёрнута в ephemeral cache-блок) или уже готовый
    список блоков (caller сам управляет кешем).
    `extra` — любые дополнительные kwargs `messages.create` (tools, stop_sequences и т.п.).
    """
    if not messages:
        raise ValueError("messages must be non-empty")
    model_id = model or DEFAULT_MODEL
    system_blocks = _coerce_system(system)

    cfg = RetryConfig(
        worker=worker,
        video_id=video_id,
        non_retryable=_NON_RETRYABLE,
    )

    def _do() -> str:
        msg = _call(
            model=model_id,
            system=system_blocks,
            messages=messages,
            max_tokens=max_tokens,
            extra=extra,
        )
        _account_tokens(msg.usage, worker=worker, video_id=video_id)
        return _extract_text(msg)

    return run_with_retry(_do, cfg)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _strip_json_fence(text: str) -> str:
    """Снять ```json ... ``` обёртку, если модель её добавила."""
    m = _JSON_FENCE.search(text)
    return m.group(1).strip() if m else text.strip()


def complete_json(
    messages: list[dict[str, Any]],
    *,
    system: str | list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    model: str | None = None,
    worker: str = "llm",
    video_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Как complete, но парсит JSON. При плохом JSON делает ОДИН corrective retry.

    Сетевые ретраи внутри complete независимы от этого retry'а.
    """
    text = complete(
        messages,
        system=system,
        max_tokens=max_tokens,
        model=model,
        worker=worker,
        video_id=video_id,
        extra=extra,
    )
    try:
        return json.loads(_strip_json_fence(text))
    except json.JSONDecodeError as exc:
        log.warning("%s bad JSON from model, requesting correction: %s", worker, exc)
        first_err = exc

    corrective = list(messages) + [
        {"role": "assistant", "content": text},
        {
            "role": "user",
            "content": (
                "Your previous reply was not valid JSON: "
                f"{first_err}. Reply now with ONLY a valid JSON value and nothing else — "
                "no prose, no code fences."
            ),
        },
    ]
    text2 = complete(
        corrective,
        system=system,
        max_tokens=max_tokens,
        model=model,
        worker=worker,
        video_id=video_id,
        extra=extra,
    )
    return json.loads(_strip_json_fence(text2))


__all__ = ["DEFAULT_MODEL", "complete", "complete_json"]
