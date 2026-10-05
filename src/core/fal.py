"""fal.ai (Flux) клиент для генерации фонов thumbnail.

Минимум: sync text-to-image через httpx. Используется только thumbnail
воркером (E2.6).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core import costs
from src.core.config import require

log = logging.getLogger(__name__)

# schnell — самый дешёвый и быстрый Flux, ОК для thumbnail.
DEFAULT_MODEL = "fal-ai/flux/schnell"


class FalError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    s = require("epic2")
    return {
        "Authorization": f"Key {s.FAL_API_KEY}",
        "Content-Type": "application/json",
    }


def text_to_image(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    width: int = 1280,
    height: int = 720,
    num_images: int = 1,
    timeout: float = 120.0,
) -> list[str]:
    """Сгенерировать изображения. Возвращает список URL'ов."""
    url = f"https://fal.run/{model}"
    body: dict[str, Any] = {
        "prompt": prompt,
        "image_size": {"width": width, "height": height},
        "num_images": num_images,
        "enable_safety_checker": True,
    }
    r = httpx.post(url, json=body, headers=_headers(), timeout=timeout)
    if r.status_code >= 400:
        raise FalError(f"flux {r.status_code}: {r.text[:400]}")
    data = r.json()
    images = data.get("images") or []
    urls = [im.get("url") for im in images if im.get("url")]
    if not urls:
        raise FalError(f"flux returned no images: {data}")
    costs.record_fal_flux(len(urls), worker="thumbnail")
    return urls
