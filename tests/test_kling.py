"""Юнит-тесты Kling-клиента: правильный body submit, wait_for poll → COMPLETED."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.core import kling


@respx.mock
def test_submit_posts_to_queue(monkeypatch):
    monkeypatch.setenv("FAL_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    monkeypatch.setenv("CREATOMATE_API_KEY", "c")
    route = respx.post(
        "https://queue.fal.run/fal-ai/kling-video/v2/master/text-to-video"
    ).mock(return_value=httpx.Response(200, json={
        "request_id": "r-1",
        "status_url": "https://queue.fal.run/.../status",
        "response_url": "https://queue.fal.run/.../response",
    }))
    out = kling.submit("cinematic shot of a gaming mouse",
                       duration=10, aspect_ratio="16:9")
    assert out["request_id"] == "r-1"
    body = route.calls[0].request.content.decode()
    assert "cinematic shot of a gaming mouse" in body
    assert '"duration":"10"' in body or '"duration": "10"' in body


@respx.mock
def test_wait_for_completes_after_polling(monkeypatch):
    monkeypatch.setenv("FAL_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    monkeypatch.setenv("CREATOMATE_API_KEY", "c")
    monkeypatch.setattr(kling.time, "sleep", lambda _s: None)
    respx.get("https://queue.fal.run/r/status").mock(side_effect=[
        httpx.Response(200, json={"status": "IN_QUEUE"}),
        httpx.Response(200, json={"status": "IN_PROGRESS"}),
        httpx.Response(200, json={"status": "COMPLETED"}),
    ])
    respx.get("https://queue.fal.run/r/response").mock(return_value=httpx.Response(
        200, json={"video": {"url": "https://cdn.fal.media/x.mp4"}}
    ))
    url = kling.wait_for(
        {"request_id": "r", "status_url": "https://queue.fal.run/r/status",
         "response_url": "https://queue.fal.run/r/response"},
        poll=0.0, deadline_sec=10,
    )
    assert url == "https://cdn.fal.media/x.mp4"


@respx.mock
def test_wait_for_raises_on_failed(monkeypatch):
    monkeypatch.setenv("FAL_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    monkeypatch.setenv("CREATOMATE_API_KEY", "c")
    monkeypatch.setattr(kling.time, "sleep", lambda _s: None)
    respx.get("https://queue.fal.run/r/status").mock(return_value=httpx.Response(
        200, json={"status": "FAILED", "error": "content policy"}
    ))
    with pytest.raises(kling.KlingError):
        kling.wait_for(
            {"request_id": "r", "status_url": "https://queue.fal.run/r/status",
             "response_url": "https://queue.fal.run/r/response"},
            poll=0.0, deadline_sec=5,
        )


def test_submit_rejects_invalid_duration(monkeypatch):
    monkeypatch.setenv("FAL_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    monkeypatch.setenv("CREATOMATE_API_KEY", "c")
    with pytest.raises(ValueError):
        kling.submit("test", duration=7)
