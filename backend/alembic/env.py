"""Alembic environment: URL берётся из настроек приложения."""

from logging.config import fileConfig

from alembic import context
from app.core.config import get_settings
from app.core.db import Base

# Импортировать модели здесь по мере появления модулей, чтобы autogenerate
# видел метаданные:
from app.models.auth import AuditRecord, User  # noqa: F401
from app.models.agronomy import (  # noqa: F401
    AgroInput,
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    FieldVersion,
    Recommendation,
    RotationPlan,
    TreatmentPlan,
    YieldRecord,
)
from app.models.integrations import (  # noqa: F401
    Forecast,
    SatelliteScene,
    SourceJournalEntry,
    WeatherObservation,
)
from app.models.machinery import Machine, MachineJob  # noqa: F401
from app.models.references import (  # noqa: F401
    Agrochem,
    Culture,
    PredecessorRule,
    Price,
    Source,
    WeatherRule,
)
from sqlalchemy import pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_uri)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    """Служебные таблицы PostGIS не сравниваются autogenerate."""
    if type_ == "table" and name in {"spatial_ref_sys"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_database_uri,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    connectable = create_engine(settings.sqlalchemy_database_uri, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
