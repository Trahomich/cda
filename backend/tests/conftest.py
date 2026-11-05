"""Общие фикстуры тестов (паттерн задачи MVP-2, решение из #20/#22).

Тесты работают на реальном PostgreSQL + PostGIS (тот же движок, что и прод):
  - DSN тестовой БД — env DATABASE_URL_TEST, по умолчанию
    postgresql+psycopg://cda:cda_dev_password@localhost:5432/cda_test
    (хост/порт/пользователь берутся из POSTGRES_*, как в app.core.config,
    поэтому внутри docker compose работает без настроек);
  - БД cda_test пересоздаётся фикстурой session-scope, схема — create_all;
  - миграции обязаны проходить отдельно — tests/test_alembic.py;
  - если PostgreSQL недоступен, тесты пропускаются с явной причиной
    (никаких тихих прохождений; гео-функции проверяются только на PostGIS).
Изоляция тестов: после каждого теста все таблицы очищаются (TRUNCATE CASCADE).
"""

import os

# импорт моделей, чтобы create_all создал все таблицы (включая PostGIS-геометрию)
import app.models  # noqa: F401  (isort: app.models после app.core.db)
import pytest
from app.core.db import Base, get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

_TEST_DB = "cda_test"


def _test_url() -> str:
    if url := os.environ.get("DATABASE_URL_TEST"):
        return url
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "cda")
    password = os.environ.get("POSTGRES_PASSWORD", "cda_dev_password")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{_TEST_DB}"


def _create_database(admin_engine, dbname: str) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))


def _drop_database(admin_engine, dbname: str) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))


@pytest.fixture(scope="session")
def pg_admin():
    """Подключение к серверу PostgreSQL; skip с причиной, если он недоступен."""
    url = make_url(_test_url())
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, connect_args={"connect_timeout": 3})
    try:
        with admin_engine.connect():
            pass
    except OperationalError as exc:
        admin_engine.dispose()
        pytest.skip(
            f"PostgreSQL недоступен ({admin_url.render_as_string(hide_password=False)}): "
            f"{exc}. Тестам нужен PostgreSQL+PostGIS (docker compose up db), "
            "SQLite-подмена геотипов запрещена (решение #20/#22).",
            allow_module_level=False,
        )
    return admin_engine


@pytest.fixture(scope="session")
def db_engine(pg_admin):
    """Тестовая БД на PostgreSQL: пересоздаётся на сессию, схема — create_all."""
    url = make_url(_test_url())
    _create_database(pg_admin, url.database)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            conn.commit()
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        _drop_database(pg_admin, url.database)


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    """Изоляция тестов: очистка всех таблиц после каждого теста."""
    yield
    tables = ", ".join(f'"{t}"' for t in Base.metadata.tables)
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db_session) -> TestClient:
    """Клиент API поверх in-process приложения с тестовой БД (PostgreSQL)."""
    app = create_app()

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)
