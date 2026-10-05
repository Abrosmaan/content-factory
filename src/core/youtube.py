"""YouTube Data API wrapper.

Минимум: upload video (private + publishAt), set thumbnail, post pinned comment.
Credentials через refresh_token из .env. Все ошибки api retry'ются на уровне
воркера через retry.py.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from src.core.config import require

log = logging.getLogger(__name__)


def _credentials():
    from google.oauth2.credentials import Credentials  # type: ignore
    s = require("epic3")
    return Credentials(
        token=None,
        refresh_token=s.YT_REFRESH_TOKEN,
        client_id=s.YT_CLIENT_ID,
        client_secret=s.YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
        ],
    )


def _client():
    from googleapiclient.discovery import build  # type: ignore
    return build("youtube", "v3", credentials=_credentials(),
                 cache_discovery=False)


def upload_video(
    *,
    file_path: str,
    title: str,
    description: str,
    tags: list[str],
    publish_at: dt.datetime,
    privacy_status: str = "private",
    category_id: str = "20",  # 20 = Gaming
) -> str:
    """Загружает видео в YouTube. Возвращает video_id.

    `privacy_status`: до прохождения YT-аудита — 'private' (E3.3). После
    аудита (api_audit_passed=true в settings) Publisher передаст 'public'.
    """
    from googleapiclient.http import MediaFileUpload  # type: ignore

    if publish_at.tzinfo is None:
        raise ValueError("publish_at must be timezone-aware")
    body: dict[str, Any] = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags[:30],
            "categoryId": category_id,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy_status,
            "publishAt": publish_at.astimezone(dt.timezone.utc).isoformat()
                          .replace("+00:00", "Z"),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(file_path, mimetype="video/mp4",
                            chunksize=-1, resumable=False)
    req = _client().videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    resp = req.execute()
    return resp["id"]


def set_thumbnail(video_id: str, image_path: str) -> None:
    from googleapiclient.http import MediaFileUpload  # type: ignore
    media = MediaFileUpload(image_path, mimetype="image/jpeg")
    _client().thumbnails().set(videoId=video_id, media_body=media).execute()


def post_pinned_comment(video_id: str, text: str) -> str:
    """Создаёт топ-комментарий. YouTube НЕ имеет API чтобы пин-нуть его
    программно (только через UI владельца канала), но владельца канала
    мы и просим: ТЗ говорит «pinned comment» — мы публикуем top-level
    комментарий с пометкой, владелец во время проверки канала pin'ит руками.
    Возвращаем id комментария.
    """
    body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {"snippet": {"textOriginal": text}},
        },
    }
    resp = _client().commentThreads().insert(part="snippet", body=body).execute()
    return resp["id"]


__all__ = ["upload_video", "set_thumbnail", "post_pinned_comment"]
