"""OAuth-helper для YouTube Data API.

Запускается на ноутбуке владельца (не на сервере), потому что нужен браузер
для согласия. После прохождения flow печатает `YT_REFRESH_TOKEN`, который
нужно вставить в `.env`.

Использование:
    # YT_CLIENT_ID / YT_CLIENT_SECRET должны быть заполнены в .env
    python -m src.cli.yt_auth

Опции:
    --port 8765        порт локального redirect-сервера
    --json secret.json вместо ENV — взять client_id/secret из JSON,
                       выгруженного из Google Cloud Console
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# oauthlib по умолчанию требует HTTPS для callback. На локальном flow с
# http://localhost OAuth2 client всё равно безопасен (loopback), включаем
# стандартный workaround для desktop installed-flow.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from src.core.config import get_settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _client_config_from_env() -> dict:
    s = get_settings()
    if not s.YT_CLIENT_ID or not s.YT_CLIENT_SECRET:
        raise SystemExit(
            "YT_CLIENT_ID / YT_CLIENT_SECRET пусты. Заполни .env или используй --json."
        )
    return {
        "installed": {
            "client_id": s.YT_CLIENT_ID,
            "client_secret": s.YT_CLIENT_SECRET,
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _client_config_from_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    # Google Cloud Console экспортирует {"installed": {...}}; принимаем и плоский dict
    if "installed" in data or "web" in data:
        return data
    return {"installed": data}


def run_flow(client_config: dict, port: int) -> str:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit(
            "google-auth-oauthlib не установлен. "
            "Запусти: pip install -r requirements.txt"
        ) from exc

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    flow.redirect_uri = f"http://localhost:{port}/"

    # Один вызов authorization_url — единая state в flow. Потом ловим callback
    # вручную, чтобы не было двойной генерации state'а run_local_server'ом.
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    url_path = Path("/tmp/yt_auth_url.txt")
    url_path.write_text(auth_url + "\n", encoding="utf-8")
    print("=" * 64, flush=True)
    print("OPEN THIS URL IN BROWSER (also saved to /tmp/yt_auth_url.txt):",
          flush=True)
    print(auth_url, flush=True)
    print("=" * 64, flush=True)
    try:
        import webbrowser
        webbrowser.open(auth_url, new=2)
    except Exception:
        pass

    # Ручной HTTP-сервер на один redirect.
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured: dict[str, str] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # type: ignore[override]
            captured["path"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Done. You may close this tab.</h2></body></html>"
            )

        def log_message(self, *args, **kwargs) -> None:  # quiet
            pass

    server = HTTPServer(("127.0.0.1", port), _Handler)
    print(f"[yt_auth] listening on http://127.0.0.1:{port}/, waiting for callback…",
          flush=True)
    server.handle_request()
    server.server_close()

    callback_path = captured.get("path", "")
    if not callback_path:
        raise SystemExit("[yt_auth] callback не пришёл")

    # Передаём весь URL в flow — он сам распарсит code+state и сверит со своим state.
    callback_url = f"http://localhost:{port}{callback_path}"
    flow.fetch_token(authorization_response=callback_url)
    creds = flow.credentials

    if not creds.refresh_token:
        raise SystemExit(
            "Google не вернул refresh_token. Зайди в "
            "https://myaccount.google.com/permissions, отзови доступ приложения "
            "и повтори — Google отдаёт refresh_token только при первом согласии."
        )
    return creds.refresh_token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--json", type=Path, help="путь к client_secret.json")
    args = parser.parse_args(argv)

    config = _client_config_from_json(args.json) if args.json else _client_config_from_env()
    token = run_flow(config, args.port)
    print()
    print("=" * 60)
    print("YT_REFRESH_TOKEN (вставь в .env):")
    print()
    print(token)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
