"""Конфигурация приложения (pydantic-settings, значения из .env / окружения)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ЦДА backend"
    env: str = "local"
    log_level: str = "INFO"

    # PostgreSQL 18 + PostGIS
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cda"
    postgres_user: str = "cda"
    postgres_password: str = "cda_dev_password"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    api_v1_prefix: str = "/api/v1"

    # Аутентификация (MVP-4)
    jwt_secret: str = "dev-only-secret-change-me"  # в prod обязательно задаётся через .env
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60

    @property
    def sqlalchemy_database_uri(self) -> str:
        """DSN для SQLAlchemy (psycopg 3)."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """Синглтон настроек; кэшируется, чтобы .env читался один раз."""
    return Settings()
