"""Дымовой e2e-тест (задача MVP-18): сквозной сценарий против docker-compose.

Запуск: `SMOKE=1 make test` (или SMOKE=1 python -m pytest tests/test_smoke_e2e.py).
Требует поднятого окружения: `make up` (api на :8000, PostgreSQL/PostGIS на :5432;
миграции применяются при старте контейнера api).

Если SMOKE не задан — тест пропускается. Если окружение недоступно —
пропускается с явной причиной (никаких тихих прохождений).

Сценарий: GET /healthz → 200; POST /api/v1/auth/login (админ, создаётся
фикстурой напрямую в БД, идемпотентно) → token; GET /api/v1/auth/me → 200.
"""

import os

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.skipif(
    os.getenv("SMOKE") != "1",
    reason="дымовой e2e: запускается только с SMOKE=1 против docker compose окружения (make up)",
)

SMOKE_BASE_URL = os.getenv("SMOKE_BASE_URL", "http://localhost:8000")
SMOKE_DATABASE_URL = os.getenv(
    "SMOKE_DATABASE_URL",
    "postgresql+psycopg://cda:cda_dev_password@localhost:5432/cda",
)
SMOKE_ADMIN_LOGIN = os.getenv("SMOKE_ADMIN_LOGIN", "smoke_admin")
SMOKE_ADMIN_PASSWORD = os.getenv("SMOKE_ADMIN_PASSWORD", "smoke-password-1")


@pytest.fixture(scope="module")
def api_client() -> httpx.Client:
    """Клиент API; при недоступном окружении — явный skip с причиной."""
    client = httpx.Client(base_url=SMOKE_BASE_URL, timeout=10.0)
    try:
        probe = client.get("/healthz")
        probe.raise_for_status()
    except httpx.HTTPError as exc:
        client.close()
        pytest.skip(f"API недоступен на {SMOKE_BASE_URL} (сначала `make up`): {exc}")
    return client


@pytest.fixture(scope="module")
def smoke_admin() -> dict[str, str]:
    """Тестовый администратор: создаётся в БД напрямую, идемпотентно."""
    # импорты внутри фикстуры — чтобы без SMOKE=1 не тянуть приложение
    from app.core.security import hash_password
    from app.models.auth import User, UserRole

    try:
        engine = create_engine(SMOKE_DATABASE_URL)
        with engine.begin() as conn:
            exists = conn.execute(
                select(User.id).where(User.login == SMOKE_ADMIN_LOGIN)
            ).scalar_one_or_none()
            if exists is None:
                conn.execute(
                    User.__table__.insert().values(
                        fio="Дымовой тест (SMOKE)",
                        login=SMOKE_ADMIN_LOGIN,
                        password_hash=hash_password(SMOKE_ADMIN_PASSWORD),
                        role=UserRole.ADMIN.value,
                        is_active=True,
                    )
                )
        engine.dispose()
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL недоступна на {SMOKE_DATABASE_URL} (сначала `make up`): {exc}")
    return {"login": SMOKE_ADMIN_LOGIN, "password": SMOKE_ADMIN_PASSWORD}


def test_healthz(api_client: httpx.Client) -> None:
    resp = api_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_login_and_me(api_client: httpx.Client, smoke_admin: dict[str, str]) -> None:
    resp = api_client.post("/api/v1/auth/login", json=smoke_admin)
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    assert token, "API-46 должен вернуть JWT"

    resp = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()  # API-47: {id, name, role}
    assert body["role"] == "admin"
    assert body["name"]
