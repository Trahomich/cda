"""Общие фикстуры тестов."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.main import create_app
# импорт моделей, чтобы create_all создал их таблицы
import app.models.auth  # noqa: F401


@pytest.fixture
def db_engine():
    """In-memory SQLite: одна схема на все потоки (StaticPool) на каждый тест.

    Без StaticPool эндпоинты (sync, в threadpool) получали бы отдельную пустую БД.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db_session) -> TestClient:
    """Клиент API поверх in-process приложения с подменённой БД (SQLite)."""
    app = create_app()

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)
