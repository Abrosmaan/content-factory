"""Creatomate API client.

Минимум: create_render(source, modifications) → poll status → готовый URL.
Используется Assembler'ом (E2.5) и Thumbnail-воркером (E2.6).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from src.core import costs
from src.core.config import project_root, require

log = logging.getLogger(__name__)

BASE_URL = "https://api.creatomate.com/v1"


class CreatomateError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    s = require("epic2")
    return {
        "Authorization": f"Bearer {s.CREATOMATE_API_KEY}",
        "Content-Type": "application/json",
    }


def load_template(name: str) -> dict[str, Any]:
    """Загрузить JSON-шаблон из templates/creatomate/<name>.json."""
    p = project_root() / "templates" / "creatomate" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def create_render(
    *,
    source: dict[str, Any] | None = None,
    template_id: str | None = None,
    modifications: dict[str, Any] | None = None,
    output_format: str = "mp4",
    output_width: int | None = None,
    output_height: int | None = None,
    frame_rate: int | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Создать render через inline source ИЛИ pre-built template_id."""
    if (source is None) == (template_id is None):
        raise CreatomateError("create_render: exactly one of source/template_id required")
    body: dict[str, Any] = {"output_format": output_format}
    if source is not None:
        body["source"] = source
    if template_id is not None:
        body["template_id"] = template_id
    if modifications:
        body["modifications"] = modifications
    if output_width is not None:
        body["width"] = output_width
    if output_height is not None:
        body["height"] = output_height
    if frame_rate is not None:
        body["frame_rate"] = frame_rate
    r = httpx.post(
        f"{BASE_URL}/renders", json=body, headers=_headers(), timeout=timeout,
    )
    if r.status_code >= 400:
        raise CreatomateError(f"create_render {r.status_code}: {r.text[:500]}")
    data = r.json()
    if isinstance(data, list):
        return data[0]
    return data


def get_render(render_id: str) -> dict[str, Any]:
    r = httpx.get(
        f"{BASE_URL}/renders/{render_id}", headers=_headers(), timeout=20.0,
    )
    if r.status_code >= 400:
        raise CreatomateError(f"get_render {r.status_code}: {r.text[:300]}")
    return r.json()


def wait_for_render(render_id: str, *, poll: float = 5.0,
                    deadline_sec: float = 600.0) -> dict[str, Any]:
    """Опрос статуса рендера до 'succeeded' / 'failed' / таймаута."""
    start = time.monotonic()
    while True:
        info = get_render(render_id)
        status = info.get("status")
        if status == "succeeded":
            return info
        if status in {"failed", "cancelled"}:
            raise CreatomateError(
                f"render {render_id} {status}: {info.get('error_message') or info}"
            )
        if time.monotonic() - start > deadline_sec:
            raise CreatomateError(f"render {render_id} timeout (status={status})")
        time.sleep(poll)


def render_and_wait(
    *,
    source: dict[str, Any] | None = None,
    template_id: str | None = None,
    modifications: dict[str, Any] | None = None,
    output_format: str = "mp4",
    output_width: int | None = None,
    output_height: int | None = None,
    frame_rate: int | None = None,
    poll: float = 5.0,
    deadline_sec: float = 600.0,
) -> dict[str, Any]:
    initial = create_render(
        source=source,
        template_id=template_id,
        modifications=modifications,
        output_format=output_format,
        output_width=output_width,
        output_height=output_height,
        frame_rate=frame_rate,
    )
    rid = initial.get("id")
    if not rid:
        raise CreatomateError(f"render create returned no id: {initial}")
    if initial.get("status") == "succeeded" and initial.get("url"):
        _record_cost(initial)
        return initial
    final = wait_for_render(rid, poll=poll, deadline_sec=deadline_sec)
    _record_cost(final)
    return final


def _record_cost(result: dict[str, Any]) -> None:
    """Списать стоимость по реальной длительности из ответа Creatomate."""
    try:
        duration = float(result.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        costs.record_creatomate(
            duration, worker="creatomate", job_id=str(result.get("id") or ""),
        )


__all__ = [
    "BASE_URL",
    "CreatomateError",
    "load_template",
    "create_render",
    "get_render",
    "wait_for_render",
    "render_and_wait",
]
