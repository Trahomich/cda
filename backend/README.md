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
├── alembic/             # миграции (0001_init каркас; 0002_auth — dat_user, dat_audit)
├── docker-compose.yml   # api + db (postgis/postgis:18-3.5) + redis, healthchecks
├── docker/db-init/      # CREATE EXTENSION postgis при первом старте БД
├── tests/               # pytest (smoke: /healthz, RFC 7807)
├── .env.example         # пример настроек
├── .pre-commit-config.yaml  # ruff (+format), mypy (мягко)
├── Dockerfile
└── pyproject.toml       # зависимости, настройки ruff/mypy/pytest
```

## Запуск (docker compose)

```bash
cd backend
cp .env.example .env                 # при необходимости поправьте пароль
docker compose up -d --build
curl http://localhost:8000/healthz   # {"status":"ok"}
```

При старте контейнера `api` автоматически выполняется `alembic upgrade head`.

Пароль БД задаётся в `.env` (`POSTGRES_PASSWORD`), по умолчанию dev-значение.

## Разработка и тесты

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
- Первый администратор: `python -m app.cli create-admin --login admin --password ...`.
- Доменные роутеры защищаются зависимостью из `app.core.rbac`:
  `APIRouter(dependencies=[Depends(group_checker("fields"))])` — группы ролей
  соответствуют матрице прав docs/07-ux.md §5. Для отдельных эндпоинтов —
  `user: CurrentUser` + `role_checker(...)`.
- Все входы (успешные и нет) пишутся в `dat_audit` (action = login / login_failed).
