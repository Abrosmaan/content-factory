"""fal.ai Kling video client.

Используется footage_generator (E8.4) для генерации 5-10с клипов
1080p из scene_description.

API (fal.ai async pattern):
  POST https://queue.fal.run/fal-ai/kling-video/v2/master/text-to-video
    → {"request_id": "...", "status_url": "...", "response_url": "..."}
  Poll GET status_url до status='COMPLETED' / 'FAILED'
  Then GET response_url → {"video": {"url": "https://..."}}
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from src.core import costs
from src.core.config import require

log = logging.getLogger(__name__)

QUEUE_BASE = "https://queue.fal.run"
# Master = высокое качество, Standard = быстрее/дешевле. v2/master OK для production.
DEFAULT_MODEL = "fal-ai/kling-video/v2/master/text-to-video"


class KlingError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    s = require("epic2")
    return {
        "Authorization": f"Key {s.FAL_API_KEY}",
        "Content-Type": "application/json",
    }


def submit(
    prompt: str,
    *,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    model: str = DEFAULT_MODEL,
    negative_prompt: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Поставить задачу в очередь fal.ai. Возвращает request_id + URLs."""
    if duration not in (5, 10):
        raise ValueError(f"Kling duration must be 5 or 10, got {duration}")
    body: dict[str, Any] = {
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    r = httpx.post(f"{QUEUE_BASE}/{model}", json=body, headers=_headers(),
                   timeout=timeout)
    if r.status_code >= 400:
        raise KlingError(f"submit {r.status_code}: {r.text[:400]}")
    data = r.json()
    if "request_id" not in data:
        raise KlingError(f"submit returned no request_id: {data}")
    return data


def get_status(status_url: str, *, timeout: float = 20.0) -> dict[str, Any]:
    r = httpx.get(status_url, headers=_headers(), timeout=timeout)
    if r.status_code >= 400:
        raise KlingError(f"status {r.status_code}: {r.text[:300]}")
    return r.json()


def get_result(response_url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    r = httpx.get(response_url, headers=_headers(), timeout=timeout)
    if r.status_code >= 400:
        raise KlingError(f"result {r.status_code}: {r.text[:300]}")
    return r.json()


def wait_for(
    initial: dict[str, Any],
    *,
    poll: float = 15.0,
    deadline_sec: float = 900.0,
) -> str:
    """Poll until COMPLETED → возвращает URL итогового mp4.
    Default deadline 15 мин (Kling master может занимать 5-10 мин на клип)."""
    request_id = initial.get("request_id")
    if not request_id:
        raise KlingError(f"wait_for: no request_id in {initial}")

    status_url = initial.get("status_url")
    response_url = initial.get("response_url")
    if not status_url or not response_url:
        raise KlingError(f"wait_for: missing urls in {initial}")

    start = time.monotonic()
    while True:
        try:
            info = get_status(status_url)
        except KlingError as exc:
            log.warning("kling status check failed (will retry): %s", exc)
            info = {"status": "IN_QUEUE"}
        status = (info.get("status") or "").upper()
        if status == "COMPLETED":
            result = get_result(response_url)
            video = (result.get("video") or {})
            url = video.get("url")
            if not url:
                raise KlingError(f"kling: completed without video url: {result}")
            return url
        if status in {"FAILED", "ERROR"}:
            raise KlingError(f"kling {status}: {info}")
        if time.monotonic() - start > deadline_sec:
            raise KlingError(f"kling timeout (status={status}, request={request_id})")
        time.sleep(poll)


def text_to_video(
    prompt: str,
    *,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    model: str = DEFAULT_MODEL,
    poll: float = 15.0,
    deadline_sec: float = 900.0,
) -> str:
    """submit + wait_for одним шагом. Возвращает URL итогового mp4."""
    initial = submit(prompt, duration=duration, aspect_ratio=aspect_ratio,
                     model=model)
    url = wait_for(initial, poll=poll, deadline_sec=deadline_sec)
    costs.record_fal_kling(
        float(duration), worker="footage_generator",
        job_id=str(initial.get("request_id") or ""),
    )
    return url


__all__ = [
    "DEFAULT_MODEL",
    "KlingError",
    "submit",
    "get_status",
    "get_result",
    "wait_for",
    "text_to_video",
]
