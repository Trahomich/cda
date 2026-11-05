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
    farm_name: str = "КФХ «Восход»"  # наименование хозяйства в ответе API-46

    # Погода (MVP-6, docs/05-integrations.md §2/§4/§6)
    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1"
    weather_poll_enabled: bool = False  # true — в docker-compose api
    weather_poll_current_minutes: int = 30
    weather_poll_forecast_hours: int = 3
    weather_forecast_days: int = 7  # горизонт хранения (контракт API-07/29)
    weather_retry_attempts: int = 5  # ретраи с экспоненциальной задержкой (05 §6)
    weather_retry_base_seconds: float = 60.0  # 0 — без пауз (тесты)
    weather_request_timeout: float = 10.0

    # Спутник (MVP-10, docs/05 §2 IF-SATELLITE, §4: опрос каталога раз в 6 ч)
    satellite_enabled: bool = False  # true — нужны ключи Copernicus (см. ниже)
    satellite_poll_hours: int = 6
    satellite_indices: str = "ndvi"  # список через запятую: ndvi,ndre,ndmi,savi
    satellite_lookback_days: int = 14  # окно опроса каталога
    satellite_max_cloud_pct: int = 20  # порог отбора сцен (05 §2/§5)
    satellite_retry_attempts: int = 5  # ретраи 1→6 ч (05 §4), base задаёт шаг
    satellite_retry_base_seconds: float = 3600.0
    satellite_request_timeout: float = 30.0
    satellite_raster_cache_dir: str = "/var/lib/cda/satellite"  # распакованные каналы L2A
    # Ключи Copernicus Data Space (плейсхолдеры в .env.example; в local пусто)
    copernicus_auth_url: str = (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDAS/protocol/openid-connect/token"
    )
    copernicus_search_url: str = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
    copernicus_client_id: str = ""
    copernicus_client_secret: str = ""

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
