#!/usr/bin/env bash
# Деплой ЦДА на сервер cda@192.168.99.201 (каталог ~/cda).
# Запуск из корня workspace: bash deploy/deploy.sh
# Секреты (POSTGRES_PASSWORD, JWT_SECRET, TUNNEL_TOKEN и пр.) лежат на сервере
# в ~/cda/.env и создаются один раз вручную (см. docs/deploy/README.md) — НЕ в git.
set -euo pipefail

SERVER=cda@192.168.99.201
REMOTE_DIR='cda'
COMPOSE="docker compose -f $REMOTE_DIR/deploy/docker-compose.prod.yml --env-file $REMOTE_DIR/.env"

echo "==> Проверка .env на сервере"
ssh "$SERVER" "test -f $REMOTE_DIR/.env || { echo 'НЕТ ~/cda/.env — создайте по docs/deploy/README.md'; exit 1; }"

echo "==> Синхронизация кода"
rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.mypy_cache' --exclude '.ruff_cache' --exclude 'core.*' \
  --exclude '.env' --exclude 'node_modules' --exclude 'test-results' \
  backend/ "$SERVER:$REMOTE_DIR/backend/"
rsync -a --delete frontend/dist/ "$SERVER:$REMOTE_DIR/frontend/dist/"
rsync -a deploy/ "$SERVER:$REMOTE_DIR/deploy/"

echo "==> Сборка и запуск стека"
ssh "$SERVER" "$COMPOSE up -d --build"

echo "==> Ожидание healthz"
for i in $(seq 1 30); do
  if ssh "$SERVER" "curl -sf http://127.0.0.1:8080/healthz >/dev/null"; then
    echo "OK: /healthz отвечает через caddy"
    exit 0
  fi
  sleep 5
done
echo "ОШИБКА: /healthz не отвечает; последние логи:" >&2
ssh "$SERVER" "$COMPOSE logs --tail 50 api db caddy" >&2
exit 1
