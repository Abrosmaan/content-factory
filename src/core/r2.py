"""Cloudflare R2 (S3-совместимый) клиент на boto3.

Один кеш-инстанс на процесс, ленивая инициализация.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config

from src.core.config import require

R2_ENDPOINT_TEMPLATE = "https://{account}.r2.cloudflarestorage.com"


@lru_cache(maxsize=1)
def client() -> Any:
    s = require("epic1")
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_TEMPLATE.format(account=s.R2_ACCOUNT_ID),
        aws_access_key_id=s.R2_ACCESS_KEY_ID,
        aws_secret_access_key=s.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def upload_file(bucket: str, key: str, path: str, content_type: str | None = None) -> None:
    extra: dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    client().upload_file(Filename=path, Bucket=bucket, Key=key, ExtraArgs=extra or None)


def signed_url(bucket: str, key: str, *, ttl_seconds: int = 7 * 24 * 3600) -> str:
    """Подписанный URL для предпросмотра (используется в Telegram-карточках, см. E2.7).

    Default TTL — 7 дней, что соответствует правилу 7 раздела 10 ТЗ.
    """
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )


def object_exists(bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
