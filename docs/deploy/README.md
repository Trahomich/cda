# Развёртывание ЦДА (прод)

Стек и сервер:
- Сервер: `ssh cda@192.168.99.201` (Debian 13, docker 26.x, compose v2; sudo недоступен — всё в домашнем каталоге).
- Каталоги на сервере: `~/cda/backend` (код API), `~/cda/frontend/dist` (собранная статика PWA), `~/cda/deploy` (compose + Caddyfile), `~/cda/.env` (секреты, **вне git**).
- Сервисы (deploy/docker-compose.prod.yml): api (FastAPI, миграции при старте), db (postgis/postgis:18-3.6 — тега 18-3.5 в Docker Hub нет), redis (7-alpine), caddy (статика + прокси `/api/*` и `/healthz`), cloudflared (Cloudflare Tunnel).

## Первичная настройка (один раз)

На сервере создать `~/cda/.env` (права 600, не копировать в git):

```
POSTGRES_DB=cda
POSTGRES_USER=cda
POSTGRES_PASSWORD=<openssl rand -hex 16>
JWT_SECRET=<openssl rand -hex 32>
FARM_NAME=КФХ «Восход»
OLLAMA_BASE_URL=            # ADR-08: внешний Ollama; пусто — ИИ-функции выключены (REQ-NF-009)
SATELLITE_ENABLED=false     # включить при наличии кредов Copernicus
TUNNEL_TOKEN=<JSON-токен туннеля Cloudflare>
```

Токен туннеля передаётся пользователем; хранится только здесь.

## Публикация наружу (Cloudflare Tunnel)

Туннель запускается по токену (remotely-managed): ingress настраивается
в Cloudflare Zero Trust → Networks → Tunnels → <туннель> → Public Hostname.
Добавить hostname (например `cda.<домен>`) со службой `http://caddy:8080`.
После этого приложение доступно по `https://cda.<домен>`.

## Обновление развёртывания

Из корня workspace: `bash deploy/deploy.sh` — rsync кода, `compose up -d --build`,
ожидание `healthz`. Демо-данные (fixtures #31): `ssh cda@192.168.99.201
'docker compose -f cda/deploy/docker-compose.prod.yml --env-file cda/.env exec api python -m app.cli seed-demo'` (уточняется по факту MVP-13).

## Проверки

- Локально на сервере: `curl -f http://127.0.0.1:8080/healthz` (через caddy) и `curl -f http://127.0.0.1:8000/healthz` (напрямую api).
- Наружу: `curl -f https://<публичный-URL>/healthz`.
- Дымовые e2e: `make smoke SMOKE_BASE_URL=https://<публичный-URL>` (задача #36).
- Логи: `docker compose -f ~/cda/deploy/docker-compose.prod.yml logs -f [api|db|caddy|cloudflared]`.

## Заметки

- postgres в prod не публикуется наружу (только внутри compose-сети); redis аналогично.
- api публикуется на `127.0.0.1:8000` только для проверок с сервера.
- БД-данные в named volume `pgdata`; при пересборке сохраняются.
