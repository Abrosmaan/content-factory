#!/usr/bin/env bash
# Прогон E2E dry-run на проде (внутри docker compose exec на VPS).
# Two формата (short + long_gaming) + полный pytest на проде.
#
#   make dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo ".env missing in $ROOT" >&2
  exit 1
fi
eval "$(grep -E '^(SSH_HOST|SSH_USER|SSH_PRIVATE_KEY_PATH)=' .env)"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH/#\~/$HOME}"
SSH_OPTS=(-i "$SSH_PRIVATE_KEY_PATH" -o ConnectTimeout=10)
REMOTE="${SSH_USER}@${SSH_HOST}"

echo "[dry-run] target: $REMOTE"

ssh "${SSH_OPTS[@]}" "$REMOTE" 'cd /opt/content-factory && \
  docker compose exec -T app python -m src.cli.dry_run --format short && \
  docker compose exec -T app python -m src.cli.dry_run --format long_gaming'
