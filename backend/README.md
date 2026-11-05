# Backend MVP ИС «Цифровой двойник агронома» (ЦДА)

Каркас backend по docs/02-architecture.md §4 и CONTRACTS.md (§5, §8).
Стек: Python 3.12 + FastAPI, PostgreSQL 18 + PostGIS, Redis, SQLAlchemy 2 + Alembic.

## Структура

```
backend/
├── app/
│   ├── main.py          # приложение FastAPI, /healthz
│   ├── api/v1.py        # роутер /api/v1 (эндпоинты добавляют модули)
│   ├── api/deps.py      # зависимости: текущий пользователь из JWT (MVP-4)
│   ├── cli.py           # python -m app.cli create-admin (MVP-4)
│   ├── core/            # config, db, errors (RFC 7807), security (argon2/JWT), rbac
│   ├── models/          # ORM-модели (auth: dat_user, dat_audit)
│   └── modules/         # доменные модули: auth, fields, rotation, inputs, weather, satellite, machinery
├── alembic/             # миграции: 0001 init, 0002 auth, 0003–0006 схема ядра (MVP-2)
├── docker-compose.yml   # api + db (postgis/postgis:18-3.5) + redis, healthchecks
├── docker/db-init/      # CREATE EXTENSION postgis при первом старте БД
├── tests/               # pytest (smoke: /healthz, RFC 7807)
├── .env.example         # пример настроек
├── .pre-commit-config.yaml  # ruff (+format), mypy (мягко)
├── Dockerfile
└── pyproject.toml       # зависимости, настройки ruff/mypy/pytest
```

## Единые команды проверок (Makefile)

```bash
make up          # docker compose up -d --build (api + PostgreSQL/PostGIS + Redis)
make migrate     # alembic upgrade head в контейнере api
make seed        # демо-хозяйство — цель-заглушка до задачи #31 (MVP-13)
make test        # pytest (юнит/интеграционные, tests/)
make smoke       # дымовой e2e против живого окружения (tests_e2e/, SMOKE=1)
make lint        # ruff check + ruff format --check + mypy app
make fmt         # автоправка ruff (фиксы + формат)
make check       # lint + test — полный прогон для CI и ревью
make down        # остановить окружение (данные в томах сохраняются)
```

Дымовой e2e-тест (tests_e2e/test_smoke_e2e.py) гоняется против поднятого
docker-compose окружения: `make up && make smoke`. Сценарий:
`/healthz` → 200; вход тестового администратора (создаётся фикстурой в БД)
→ JWT; `GET /auth/me` → 200. Без `SMOKE=1` тест пропускается; при
недоступном окружении — skip с явной причиной. Адреса переопределяются
переменными `SMOKE_BASE_URL`, `SMOKE_DATABASE_URL`, `SMOKE_ADMIN_LOGIN`,
`SMOKE_ADMIN_PASSWORD`.

## Запуск (docker compose)

```bash
cd backend
cp .env.example .env                 # при необходимости поправьте пароль
docker compose up -d --build
curl http://localhost:8000/healthz   # {"status":"ok"}
```

При старте контейнера `api` автоматически выполняется `alembic upgrade head`.

Пароль БД задаётся в `.env` (`POSTGRES_PASSWORD`), по умолчанию dev-значение.

## Схема БД и справочники (MVP-2)

Миграции `0003_references`…`0006_integrations` создают справочники `ref_*`
и таблицы ядра `dat_*` по docs/06-data-api.md §2–§4: агрономия (поля
с PostGIS-географией и generated column `area_ga`, севооборот, СЗР, журнал
операций, алерты, рекомендации), техника, погода/спутник/журнал источников.
ORM-модели — `app/models/` (зеркало миграций; расхождение ловит `alembic check`).

Сиды справочников (идемпотентны, повторный запуск ничего не добавит):

```bash
python -m app.cli seed-references   # ref_culture (11 культур) + ref_source (3 адаптера)
```

## Разработка и тесты

Тесты работают на реальном PostgreSQL+PostGIS (SQLite не умеет географию):
DSN — env `DATABASE_URL_TEST`, по умолчанию
`postgresql+psycopg://cda:cda_dev_password@localhost:5432/cda_test`;
БД пересоздаётся фикстурой. Если PostgreSQL недоступен, тесты пропускаются
с явной причиной. Цепочка миграций проверяется tests/test_alembic.py
(`alembic upgrade head` на чистой БД). В docker compose: `docker compose up -d db`.

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e . --group dev        # или: pip install fastapi uvicorn ... && pip install pytest httpx
pytest                              # тесты не требуют БД/Redis
uvicorn app.main:app --reload       # локальный сервер без docker
alembic revision --autogenerate -m "..."   # новая миграция (нужна запущенная БД)
pre-commit install && pre-commit run --all-files
```

## Конвенции

- Ошибки API — только RFC 7807 (`application/problem+json`): `type`, `title`,
  `status`, `detail`, `instance` + машинный `code`. Доменные ошибки — наследники
  `app.core.errors.AppError` (422 = нарушение бизнес-правила, 400 = синтаксис).
- Коды HTTP по CONTRACTS.md §8; пагинация `?limit=50&offset=0`; POST → 201 + Location, DELETE → 204.
- Форматы данных (JSON, snake_case, UUID v4, ISO 8601) — CONTRACTS.md §5.
- Модули не пишут SQL к чужим таблицам; схемы БД меняем только через Alembic.
- Идентификаторы — латиница snake_case; комментарии — русский, по делу.

## Аутентификация и RBAC (MVP-4)

- Вход: `POST /api/v1/auth/login` (API-46) → `{token, user {name, role, farm_name}}`;
  профиль: `GET /api/v1/auth/me` (API-47) → `{id, name, role}`.
  Пароли — argon2id, токены — JWT HS256 (`JWT_SECRET`, TTL `JWT_TTL_MINUTES`).

## Модуль полей (MVP-5, API-01..05)

`app/modules/fields/` — контуры, версии и статусы полей по docs/03 §1.1 и 06 §5:

- `GET /api/v1/fields` (фильтры `status`, `q`, `with_geometry=true`, пагинация);
  в элементах — агрегат `current_cycle {year, culture_name, kind} | null`;
- `POST /fields` (201 + Location), `GET /fields/{id}` (карточка «Поле 360°»:
  версии контура, последние операции; `ndvi`/`weather` — null до MVP-6/MVP-10),
  `PATCH /fields/{id}` (смена контура создаёт версию; смена статуса — по автомату
  `active → archived|merged` с обязательным `reason`, пишется в `dat_audit`);
- `GET/POST /fields/{id}/versions` — история версий контура (REQ-F-002);
- `POST /fields/import` — GeoJSON-файл (FeatureCollection), отчёт ImportReport
  `{created, skipped, errors}` (KML/shapefile — v1; нужна зависимость python-multipart).

Площадь — геодезическая `ST_Area(geography)/10000` га (generated column `area_ga`,
03 §6.1), приёмка 2 % (REQ-F-001, тест с эталонным полигоном). Геовалидация PostGIS:
422 `invalid_geometry` (структура, незамкнутое кольцо), 422 `self_intersection`
(ST_IsValidReason), 422 `area_below_min` (< 0,1 га), 409 `duplicate_name`.
Права: чтение — группа RBAC `fields` (агроном/руководитель/механизатор/админ),
изменение — agronomist/admin (403 `insufficient_role`).

## Экспорт данных хозяйства (MVP-14, API-32)

`app/modules/export/` — `GET /api/v1/export?entity=&format=` (REQ-NF-007):
- entity из белого списка MVP: fields, rotation_plans, crop_cycles, agro_inputs,
  treatment_plans, operations, machines, machine_jobs, alerts (400 `unknown_entity`);
- format=json|csv (400 `invalid_format`); отдача потоковая (StreamingResponse,
  yield_per), CSV — «;», UTF-8 с BOM, даты ISO 8601, геометрия — GeoJSON-строка;
- роли — группа RBAC `export` (manager, admin).
- Первый администратор: `python -m app.cli create-admin --login admin --password ...`.
- Доменные роутеры защищаются зависимостью из `app.core.rbac`:
  `APIRouter(dependencies=[Depends(group_checker("fields"))])` — группы ролей
  соответствуют матрице прав docs/07-ux.md §5. Для отдельных эндпоинтов —
  `user: CurrentUser` + `role_checker(...)`.
- Все входы (успешные и нет) пишутся в `dat_audit` (action = login / login_failed).

## Модуль погоды (MVP-6, API-07/29, RULE-AGRO-09)

`app/integrations/` + `app/modules/weather/` — контур приёма Open-Meteo по
docs/05-integrations.md (IF-WEATHER):

- адаптер `OpenMeteoAdapter` (source_id `weather.open-meteo`): прогноз (≤16 сут),
  текущая погода, архив (archive-api); ветер запрашивается в м/с; валидация
  диапазонов 05 §5 (T вне [-60;+60] °C, ветер >60 м/с → `DataQualityError`,
  запись отбрасывается); сеть/5xx → `SourceUnavailableError` (ретраи
  `WEATHER_RETRY_ATTEMPTS` с экспоненциальной задержкой от
  `WEATHER_RETRY_BASE_SECONDS`); пустой ответ → `DataGapError` (не ошибка);
  hazard по порогам: t_min ≤ 0 → frost, осадки ≥ 15 мм → heavy_rain,
  ветер ≥ 15 м/с → wind;
- ingest `WeatherIngest`: активные поля → центроид `ST_Centroid` → адаптер →
  факты `dat_weather_observation` (дедуп UQ field+дата+источник), прогноз
  `dat_forecast` (последняя версия перезаписывает), журнал `dat_source_journal`
  (ok/degraded/error + причина) и `ref_source.last_status/last_success_at`;
- API: `GET /api/v1/fields/{id}/weather` (API-07: факты 14 сут + прогноз 7 сут
  + meta источника со stale-меткой: issued_at старше 12 ч), `GET
  /api/v1/weather/forecast?field_id&days` (API-29), `GET
  /api/v1/weather/windows?operation_type&field_id` — погодное окно RULE-AGRO-09:
  вердикт по суткам, причины на русском, `alert` — текст для ENT-ALERT, если
  окна нет. Права — группа RBAC `weather` (все 5 ролей, E-06/E-10);
- пороги окон — `ref_weather_rule` (params JSONB по operation_type, редактируемые
  руководителем), сид: `python -m app.cli seed-weather-rules` (идемпотентно);
  маппинги суточных прогнозов к формулировкам правил — в `app/modules/weather/rules.py`;
- фоновый опрос (05 §4): APScheduler, текущая — `WEATHER_POLL_CURRENT_MINUTES`
  (30 мин), прогноз — `WEATHER_POLL_FORECAST_HOURS` (3 ч), джобы в Redis
  (недоступен → in-memory). В local выключен (`WEATHER_POLL_ENABLED=false`),
  в docker-compose api включён.
