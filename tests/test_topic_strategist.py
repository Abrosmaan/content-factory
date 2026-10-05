"""Юнит-тесты topic_strategist: парсинг ответа LLM + промпт-сборка."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from src.workers import topic_strategist as ts


def test_build_prompt_includes_all_blocks():
    topics = [
        ts.TopicRow(id="t1", source="rss:x", title="Title A", url=None,
                    payload={"summary": "short summary"}),
        ts.TopicRow(id="t2", source="reddit:pcgaming", title="Title B",
                    url="https://r/x", payload={"selftext": "post body"}),
    ]
    prompt = ts._build_prompt(
        topics, games=["cs2", "valorant"],
        weights={"short": 1.0, "long_gaming": 1.2},
        killed=["bridge"],
    )
    assert "cs2, valorant" in prompt
    assert "bridge" in prompt
    assert "Title A" in prompt and "Title B" in prompt
    assert "t1" in prompt and "t2" in prompt
    assert '"short": 1.0' in prompt


def test_score_with_llm_chunks_topics():
    """Большой список тем должен разбиваться на CHUNK_SIZE батчей."""
    topics = [
        ts.TopicRow(id=f"t{i}", source="rss:x", title=f"T {i}", url=None,
                    payload={})
        for i in range(120)
    ]

    calls = []

    def fake_complete_json(messages, **kwargs):
        calls.append(messages[0]["content"])
        return {"scored": [{"topic_id": "t0", "score": 50,
                            "reasoning": "x", "suggested_format": "short"}]}

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    with patch.object(ts, "connection", return_value=fake_conn), \
            patch.object(ts, "_game_pool", return_value=[]), \
            patch.object(ts, "_format_weights", return_value=({}, [])), \
            patch.object(ts.llm, "complete_json",
                         side_effect=fake_complete_json):
        out = ts._score_with_llm(topics)

    # 120 / 50 = 3 чанка (50, 50, 20)
    assert len(calls) == 3
    assert len(out) == 3  # каждый чанк вернул по 1 элементу


def test_score_with_llm_continues_on_chunk_failure():
    """Если один чанк падает — другие всё равно отрабатывают."""
    topics = [
        ts.TopicRow(id=f"t{i}", source="rss:x", title=f"T {i}", url=None,
                    payload={})
        for i in range(150)
    ]

    call_idx = {"n": 0}

    def fake_complete_json(messages, **kwargs):
        call_idx["n"] += 1
        if call_idx["n"] == 2:
            raise json.JSONDecodeError("boom", "doc", 0)
        return {"scored": [{"topic_id": "t0", "score": 10,
                            "reasoning": "x", "suggested_format": "short"}]}

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    with patch.object(ts, "connection", return_value=fake_conn), \
            patch.object(ts, "_game_pool", return_value=[]), \
            patch.object(ts, "_format_weights", return_value=({}, [])), \
            patch.object(ts.llm, "complete_json",
                         side_effect=fake_complete_json):
        out = ts._score_with_llm(topics)

    # 3 чанка, средний упал → 2 успешных
    assert len(out) == 2
