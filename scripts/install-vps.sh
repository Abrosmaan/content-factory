#!/usr/bin/env bash
# Первичная настройка VPS. Запускается ОДИН раз после деплоя кода.
# Идемпотентен: повторный запуск не ломает уже настроенное.
#
#   bash scripts/install-vps.sh

set -euo pipefail

APP_DIR=/opt/content-factory
SERVICE=content-factory

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "must be run as root (try: sudo bash scripts/install-vps.sh)" >&2
    exit 1
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    echo "[install] docker already installed: $(docker --version)"
  else
    echo "[install] installing docker"
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  systemctl enable --now docker
}

install_systemd_unit() {
  cp "${APP_DIR}/deploy/${SERVICE}.service" "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload
  systemctl enable "${SERVICE}.service"
  echo "[install] systemd unit installed: ${SERVICE}.service"
}

ensure_env() {
  if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "ERROR: ${APP_DIR}/.env отсутствует. Зальёт deploy.sh — повтори деплой." >&2
    exit 1
  fi
  chmod 600 "${APP_DIR}/.env"
}

main() {
  require_root
  install_docker
  ensure_env
  cd "${APP_DIR}"
  docker compose build
  install_systemd_unit
  systemctl restart "${SERVICE}.service"
  sleep 2
  systemctl --no-pager status "${SERVICE}.service" | head -20 || true
  echo "[install] done"
}

main "$@"
