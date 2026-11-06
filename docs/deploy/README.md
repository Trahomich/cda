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
ожидание `healthz`. Затем (сидируется один раз и после изменений сидов):
`ssh cda@192.168.99.201 'cd cda && docker compose -f deploy/docker-compose.prod.yml --env-file .env exec api python -m app.cli seed-demo'`
(демо-хозяйство #31 + демо-агроном demo_agronomist/demo123) и при необходимости
`... exec api python -m app.cli create-admin --login <логин> --password <пароль>`.
Пароли/логины демо-входа — только в итоге задачи, не в коде.

## Проверки

- LAN (текущий режим): `curl -f http://192.168.99.201:8080/healthz` — приложение доступно в локальной сети.
- Локально на сервере: `curl -f http://127.0.0.1:8000/healthz` (напрямую api, только с самого сервера).
- Дымовые e2e: `make smoke SMOKE_BASE_URL=https://<публичный-URL>` (задача #36).
- Логи: `docker compose -f ~/cda/deploy/docker-compose.prod.yml logs -f [api|db|caddy|cloudflared]`.

## Заметки

- postgres в prod не публикуется наружу (только внутри compose-сети); redis аналогично.
- api публикуется на `127.0.0.1:8000` только для проверок с сервера.
- БД-данные в named volume `pgdata`; при пересборке сохраняются.

## Известная проблема: исходящие соединения cloudflared блокируются сетью (2026-11-06)

Симптом: контейнер `cloudflared` бесконечно пишет
`Unable to establish connection with Cloudflare edge error="TLS handshake with edge error: EOF"`.

Диагностика (сервер и workspace-хост ведут себя одинаково):

- TCP до `198.41.192.0/24`/`198.41.200.0/24:7844` открывается; DNS работает; `api.cloudflare.com:443` доступен.
- openssl и тестовый Go-клиент завершают TLS-рукопожатие с edge успешно (в т.ч. с ALPN `h2`/`argotunnel`, клиентским сертификатом и post-quantum группой X25519MLKEM768).
- Но ClientHello именно cloudflared (по tcpdump: hello доставлен, ACK получен) получает FIN от edge/посредника без ServerHello — через ~1.5–8 с. QUIC (UDP 7844) тоже не проходит.
- Проявляется на любой версии cloudflared (2024.12.2 … 2026.10.7), с `--protocol http2/quic/h2mux`, в docker-сети и в `--network host` — то есть фильтр на сетевом периметре (DPI/DLP), а не в docker.

Что нужно, чтобы заработало: разрешить на периметре исходящие TCP **и** UDP 7844 к Cloudflare без инспекции TLS (или внести cloudflared в исключения DLP). Стек уже настроен так, что cloudflared сам подключится сразу после открытия — он в `restart: unless-stopped` и `--protocol http2`.

После открытия доступа добавить Public Hostname в Cloudflare Zero Trust (службу указать `http://caddy:8080`) и проверить `curl -f https://<домен>/healthz`.

