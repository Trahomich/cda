"""Настройка БД: engine и сессии SQLAlchemy 2 (psycopg 3).

Доменные модули получают сессию через `get_db` (Depends).
Схему меняем только миграциями Alembic (alembic/versions).
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей ЦДА."""


settings = get_settings()

engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Зависимость FastAPI: сессия БД на время запроса."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
