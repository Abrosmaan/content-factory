"""Guard: review-карточка не отдаёт превью на отсутствующий объект в R2."""

from __future__ import annotations

from src.bot import digest as digest_mod
from src.workers import seo_packager


def test_seo_skips_preview_when_object_missing(monkeypatch, caplog):
    import logging
    sent: list[dict] = []

    class _S:
        TELEGRAM_BOT_TOKEN = "x"
        TELEGRAM_OWNER_CHAT_ID = "1"
        R2_BUCKET_RENDERS = "renders"
    monkeypatch.setattr(seo_packager, "get_settings", lambda: _S())
    monkeypatch.setattr(seo_packager, "connection",
                        lambda: _conn_with_key("renders/ghost.mp4"))
    monkeypatch.setattr(seo_packager.r2, "object_exists",
                        lambda b, k: False)
    monkeypatch.setattr(seo_packager.r2, "signed_url",
                        lambda b, k, **kw: pytest.fail("must not sign missing"))

    def _capture(**kw):
        sent.append(kw)
        return {"ok": True}
    monkeypatch.setattr("src.bot.cards.send_video_review_card", _capture)

    with caplog.at_level(logging.WARNING, logger="src.workers.seo_packager"):
        seo_packager._push_review_card("v-1", "Title", "Body desc")

    assert len(sent) == 1
    assert sent[0]["preview_url"] is None
    assert any("missing in R2" in r.message for r in caplog.records)


def test_seo_includes_preview_when_object_exists(monkeypatch):
    sent: list[dict] = []

    class _S:
        TELEGRAM_BOT_TOKEN = "x"
        TELEGRAM_OWNER_CHAT_ID = "1"
        R2_BUCKET_RENDERS = "renders"
    monkeypatch.setattr(seo_packager, "get_settings", lambda: _S())
    monkeypatch.setattr(seo_packager, "connection",
                        lambda: _conn_with_key("renders/ok.mp4"))
    monkeypatch.setattr(seo_packager.r2, "object_exists", lambda b, k: True)
    monkeypatch.setattr(seo_packager.r2, "signed_url",
                        lambda b, k, **kw: f"signed://{b}/{k}")
    monkeypatch.setattr("src.bot.cards.send_video_review_card",
                        lambda **kw: sent.append(kw) or {"ok": True})

    seo_packager._push_review_card("v-2", "Title", "Body desc")
    assert sent[0]["preview_url"] == "signed://renders/renders/ok.mp4"


def test_digest_skips_missing_preview(monkeypatch, caplog):
    import logging
    sent: list[dict] = []

    class _S:
        R2_BUCKET_RENDERS = "renders"
    monkeypatch.setattr(digest_mod, "get_settings", lambda: _S())
    monkeypatch.setattr(digest_mod, "build_summary", lambda: "summary")
    monkeypatch.setattr(digest_mod, "_send_text", lambda text: None)
    monkeypatch.setattr(digest_mod, "_pending_topics", lambda limit=10: [])
    monkeypatch.setattr(digest_mod, "_ready_videos", lambda: [{
        "id": "v-1", "title": "T", "seo": {"description": "Body"},
        "render_key": "renders/ghost.mp4",
    }])
    monkeypatch.setattr(digest_mod.r2, "object_exists", lambda b, k: False)
    monkeypatch.setattr(digest_mod.r2, "signed_url",
                        lambda b, k, **kw: pytest.fail("must not sign"))
    monkeypatch.setattr(digest_mod, "send_video_review_card",
                        lambda **kw: sent.append(kw) or {"ok": True})

    with caplog.at_level(logging.WARNING, logger="src.bot.digest"):
        digest_mod.send_digest()

    assert sent[0]["preview_url"] is None
    assert any("missing in R2" in r.message for r in caplog.records)


# ---------- helpers ----------

import pytest


def _conn_with_key(key):
    class _Cur:
        def execute(self, sql, params=None):
            pass
        def fetchone(self):
            return (key,)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    class _Conn:
        def cursor(self):
            return _Cur()
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _Conn()
