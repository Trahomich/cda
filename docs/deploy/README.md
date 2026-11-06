# Развёртывание ЦДА (прод)

Стек и сервер:
- Сервер: `ssh cda@192.168.99.201` (Debian 13, docker 26.x, compose v2; sudo недоступен — всё в домашнем каталоге).
- Каталоги на сервере: `~/cda/backend` (код API), `~/cda/frontend/dist` (собранная статика PWA), `~/cda/deploy` (compose + Caddyfile), `~/cda/.env` (секреты, **вне git**).
- Сервисы (deploy/docker-compose.prod.yml): api (FastAPI, миграции при старте), db (postgis/postgis:18-3.6 — тега 18-3.5 в Docker Hub нет), redis (7-alpine), caddy (статика + прокси `/api/*` и `/healthz`), cloudflared (Cloudflare Tunnel), weather-ingest (воркер приёма погоды Open-Meteo — тот же образ, что api, запуск `python -m app.services.weather_ingest`, ADR-09: api только читает), sentinel-ingest (воркер каталогизации Sentinel-2: STAC earth-search → БД + NDVI по COG, запуск `python -m app.services.sentinel_ingest`; анонимно, без кредов Copernicus). Интервалы ингеста — env `INGEST_*` (CONTRACTS.md §7, см. ниже).

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

### Env ингест-воркеров (CONTRACTS.md §7, ADR-09)

Значения по умолчанию зашиты в коде (backend/app/core/config.py); в
docker-compose.prod.yml воркеры уже настроены, менять не требуется.

| Переменная | Default | Назначение |
|---|---|---|
| `OPEN_METEO_BASE_URL` | `https://api.open-meteo.com/v1` | базовый URL API Open-Meteo (погода/прогноз) |
| `STAC_API_URL` | `https://earth-search.aws.element84.com/v1` | каталог STAC Sentinel-2 (earth-search, анонимно) |
| `INGEST_RUN_ONCE` | `false` | `true` — один цикл всех джоб и выход (тесты/разовый прогон) |
| `INGEST_WEATHER_CURRENT_MINUTES` | `30` | периодичность джобы текущей погоды (weather-ingest) |
| `INGEST_WEATHER_FORECAST_HOURS` | `3` | периодичность джобы прогноза |
| `INGEST_WEATHER_ARCHIVE_MINUTES` | `60` | периодичность докачки архива погоды |
| `INGEST_WEATHER_BACKFILL_DAYS` | `14` | окно докачки архива назад |
| `INGEST_SATELLITE_CATALOG_HOURS` | `6` | периодичность опроса STAC-каталога (sentinel-ingest) |
| `INGEST_SATELLITE_BACKFILL_DAYS` | `14` | окно поиска сцен Sentinel-2 назад |

Также `INGEST_SATELLITE_REQUEST_TIMEOUT`, `INGEST_SATELLITE_RETRY_ATTEMPTS`,
`INGEST_SATELLITE_RETRY_BASE_SECONDS`, `INGEST_SATELLITE_NDVI_BATCH`,
`INGEST_SATELLITE_NDVI_WORKERS` (таймауты/ретраи/параметры расчёта NDVI).
Fake-адаптеры остаются в тестах и dev-режиме; на проде воркеры работают
по расписанию (`INGEST_RUN_ONCE=false`).

## Публикация наружу (Cloudflare Tunnel)

Публичный URL: `https://cda.oblaka.pw` (Public Hostname в Cloudflare Zero Trust,
службу указать `http://caddy:8080`). Приложение доступно по этому адресу,
когда стек запущен (см. ниже про обход DPI через fragrelay).

## Обновление развёртывания

Из корня workspace: `bash deploy/deploy.sh` — rsync кода, `compose up -d --build`,
ожидание `healthz`. Затем (сидируется один раз и после изменений сидов):
`ssh cda@192.168.99.201 'cd cda && docker compose -f deploy/docker-compose.prod.yml --env-file .env exec api python -m app.cli seed-demo'`
(демо-хозяйство #31, демо-агроном demo_agronomist/demo123 и демо-руководитель
demo_manager/demo123 из #46; сид идемпотентен — пользователи, справочники,
матрица предшественников и демо-препараты добавляются при каждом запуске) и при необходимости
`... exec api python -m app.cli create-admin --login <логин> --password <пароль>`.
Пароли/логины демо-входа — только в итоге задачи, не в коде.

Импорт реального хозяйства (v1-02, IF-FIELDS): SHP-архив (zip, WGS84) или
GeoJSON кладётся на сервер, копируется в контейнер api и импортируется:

```
ssh cda@192.168.99.201 'cd cda && \
  docker cp agronet.fields.zip $(docker compose -f deploy/docker-compose.prod.yml --env-file .env ps -q api):/tmp/fields.zip && \
  docker compose -f deploy/docker-compose.prod.yml --env-file .env exec api python -m app.cli import-fields /tmp/fields.zip --replace'
```

(`--replace` удаляет прежние поля с зависимостями — демо-поля заменяются
реальными; пользователи и справочники сохраняются).

## Проверки

- LAN (текущий режим): `curl -f http://192.168.99.201:8080/healthz` — приложение доступно в локальной сети.
- Локально на сервере: `curl -f http://127.0.0.1:8000/healthz` (напрямую api, только с самого сервера).
- Дымовые e2e: `make smoke SMOKE_BASE_URL=https://<публичный-URL>` (задача #36).
- Логи: `docker compose -f ~/cda/deploy/docker-compose.prod.yml logs -f [api|db|caddy|cloudflared|weather-ingest|sentinel-ingest]`.

## Заметки

- postgres в prod не публикуется наружу (только внутри compose-сети); redis аналогично.
- api публикуется на `127.0.0.1:8000` только для проверок с сервера.
- БД-данные в named volume `pgdata`; при пересборке сохраняются.

## Известная проблема: блокировка DPI api.open-meteo.com (2026-11-06) — РЕШЕНО

После пересева на реальное хозяйство воркер weather-ingest залил архив
(`archive-api.open-meteo.com`, IP 5.9.98.12), но current/forecast бесконечно
ретраились `сеть недоступна: timed out`: `api.open-meteo.com` резолвится в
157.90.83.168, и периметр режет соединения к этому IP (с того же сервера
через SOCKS-прокси пользователя — 200 OK за 0.4 с; earth-search и
sentinel-cogs доступны напрямую).

Решение: httpx (адаптеры Open-Meteo и STAC) ходит через тот же SOCKS5-прокси,
что и fragrelay: во все сервисы с исходящим httpx-трафиком (api, weather-ingest,
sentinel-ingest) добавлен env `ALL_PROXY=socks5://${SOCKS_HOST}:${SOCKS_PORT}`
(SOCKS_HOST/SOCKS_PORT — в ~/cda/.env, default 192.168.99.45:1080), в
зависимостях — `httpx[socks]`. httpx читает ALL_PROXY сам (trust_env).
Позже выяснилось: и POST /search к earth-search (sentinel-ingest) напрямую
отдаётся >60 с (DPI душит и его, GET при этом быстрый) — поэтому прокси нужен
всем трём сервисам.

## Известная проблема: исходящие соединения cloudflared блокируются DPI (2026-11-06) — РЕШЕНО

Симптом: контейнер `cloudflared` бесконечно писал
`Unable to establish connection with Cloudflare edge error="TLS handshake with edge error: EOF"`.

Диагностика (сервер и workspace-хост ведут себя одинаково):

- TCP до `198.41.192.0/24`/`198.41.200.0/24:7844` открывается; DNS работает; `api.cloudflare.com:443` доступен.
- openssl и тестовый Go-клиент завершают TLS-рукопожатие с edge успешно (в т.ч. с ALPN `h2`/`argotunnel`, клиентским сертификатом и post-quantum группой X25519MLKEM768).
- Но ClientHello именно cloudflared (по tcpdump: hello доставлен, ACK получен) получал FIN без ServerHello — через ~1.5–8 с. QUIC (UDP 7844) тоже не проходил.
- Проявлялось на любой версии cloudflared (2024.12.2 … 2026.10.7), с `--protocol http2/quic/h2mux`, в docker-сети и в `--network host`, а также через SOCKS5-прокси в LAN — то есть фильтр на сетевом периметре (DPI/DLP) матчит TLS-сигнатуру ClientHello cloudflared по содержимому.

### Решение: фрагментирующий SOCKS5-релей (deploy/fragrelay.py)

В prod-стек добавлен сервис `fragrelay`: cloudflared подключается к нему
(`--edge fragrelay:7844`, протокол http2), а релей соединяется с edge Cloudflare
(region1.v2.argotunnel.com:7844) через SOCKS5-прокси пользователя
(`192.168.99.45:1080`, настраивается env `SOCKS_HOST`/`SOCKS_PORT` из `~/cda/.env`),
пересылая первые 2048 байта потока (ClientHello) по одному байту — DPI не может
собрать сигнатуру по содержимому одного пакета. Остальной трафик идёт как есть.

Проверено 2026-11-06: `Registered tunnel connection ... protocol=http2` в логах
`deploy-cloudflared-1`. Если прокси изменится или пропадёт, туннель начнёт
переподключаться и поднимется сам после восстановления (restart: unless-stopped).

Альтернатива (если DPI на периметре отключат полностью): убрать из
`docker-compose.prod.yml` сервис `fragrelay`, флаг `--edge fragrelay:7844`
у cloudflared и зависимость от fragrelay — cloudflared подключится напрямую.

Ещё одна найденная проблема того же дня: токен туннеля в `~/cda/.env` был
с опечаткой (два символа) — давал `Failed to get tunnel` уже после прохождения
TLS. Токен копировать целиком, без ручного перепечатывания.

После открытия доступа добавить Public Hostname в Cloudflare Zero Trust (службу указать `http://caddy:8080`) и проверить `curl -f https://<домен>/healthz`.

