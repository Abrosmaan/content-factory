#!/usr/bin/env bash
# Деплой на VPS. Идемпотентен.
#
#   make deploy   # или: bash scripts/deploy.sh
#
# Требования: .env заполнен (SSH_HOST/USER/PRIVATE_KEY_PATH); rsync,
# ssh установлены локально.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo ".env missing in $ROOT" >&2
  exit 1
fi

# Push в GitHub перед деплоем — гарантия что remote main ≡ то что
# уезжает на VPS. Отключаемо через CF_NO_PREDEPLOY_PUSH=1.
if [[ "${CF_NO_PREDEPLOY_PUSH:-0}" != "1" ]]; then
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
  if [[ "$branch" != "HEAD" ]]; then
    echo "[deploy] git push origin $branch (pre-deploy)"
    if ! git push origin "$branch" 2>&1; then
      echo "[deploy] WARNING: git push failed; deploy продолжается с локальной версией." >&2
    fi
  fi
fi

# Грузим только нужные SSH-ключи. eval — потому что process substitution
# выполняется в subshell на старом bash (macOS).
eval "$(grep -E '^(SSH_HOST|SSH_USER|SSH_PRIVATE_KEY_PATH)=' .env)"

: "${SSH_HOST:?SSH_HOST missing in .env}"
: "${SSH_USER:?SSH_USER missing in .env}"
: "${SSH_PRIVATE_KEY_PATH:?SSH_PRIVATE_KEY_PATH missing in .env}"

# expand ~
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH/#\~/$HOME}"

if [[ ! -f "$SSH_PRIVATE_KEY_PATH" ]]; then
  echo "SSH key not found: $SSH_PRIVATE_KEY_PATH" >&2
  exit 1
fi

SSH_OPTS=(-i "$SSH_PRIVATE_KEY_PATH" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
REMOTE="${SSH_USER}@${SSH_HOST}"
APP_DIR=/opt/content-factory

echo "[deploy] target: $REMOTE:$APP_DIR"

ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p $APP_DIR"

# rsync кода (без секретов, .git, кеша)
rsync -avz --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.mypy_cache/' \
  --exclude 'logs/' \
  --exclude '.DS_Store' \
  --exclude 'сssh_for_dev_pet_proj*' \
  -e "ssh ${SSH_OPTS[*]}" \
  ./ "$REMOTE:$APP_DIR/"

# Секретный .env — отдельно, разрешён только владельцу
scp "${SSH_OPTS[@]}" .env "$REMOTE:$APP_DIR/.env"
ssh "${SSH_OPTS[@]}" "$REMOTE" "chmod 600 $APP_DIR/.env"

# Если systemd-юнита ещё нет — первичная установка
ssh "${SSH_OPTS[@]}" "$REMOTE" bash -s <<'REMOTE'
set -euo pipefail
if [[ ! -f /etc/systemd/system/content-factory.service ]]; then
  echo "[remote] first-time install"
  bash /opt/content-factory/scripts/install-vps.sh
else
  cd /opt/content-factory
  docker compose build
  systemctl restart content-factory.service
  sleep 2
  systemctl --no-pager status content-factory.service | head -15 || true
fi
REMOTE

echo "[deploy] done"
