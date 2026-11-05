"""Тест: цепочка Alembic-миграций проходит до head на чистой БД (MVP-2).

Миграции обязаны проходить на реальном PostgreSQL+PostGIS — это критерий
задачи #20 (alembic upgrade head без ошибок, все таблицы создаются).
Запускается в отдельной БД cda_test_migrations, чтобы не задевать cda_test.
"""

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from tests.conftest import _create_database, _drop_database, _test_url

_MIGRATIONS_DB = "cda_test_migrations"

# Таблицы, которые обязана создать цепочка миграций (06-data-api §2–§4, MVP-2)
EXPECTED_TABLES = {
    "dat_field",
    "dat_field_version",
    "dat_rotation_plan",
    "dat_crop_cycle",
    "dat_agro_input",
    "dat_treatment_plan",
    "dat_field_operation",
    "dat_yield_record",
    "dat_alert",
    "dat_recommendation",
    "dat_machine",
    "dat_machine_job",
    "dat_weather_observation",
    "dat_forecast",
    "dat_satellite_scene",
    "dat_source_journal",
    "dat_audit",  # миграция 0002_auth (MVP-4)
    "dat_user",  # миграция 0002_auth (MVP-4)
    "ref_culture",
    "ref_predecessor_matrix",
    "ref_agrochem",
    "ref_source",
    "ref_weather_rule",
    "ref_price",
}


def test_alembic_upgrade_head(pg_admin):
    url = make_url(_test_url())
    _create_database(pg_admin, _MIGRATIONS_DB)
    try:
        engine_url = url.set(database=_MIGRATIONS_DB)
        engine = create_engine(engine_url)
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            conn.commit()
        engine.dispose()

        # alembic берёт DSN из app.core.config (env POSTGRES_*)
        env = {
            **os.environ,
            "POSTGRES_DB": _MIGRATIONS_DB,
            "POSTGRES_HOST": url.host,
            "POSTGRES_PORT": str(url.port or 5432),
            "POSTGRES_USER": url.username,
            "POSTGRES_PASSWORD": url.password or "",
        }
        backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head упал:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        engine = __import__("sqlalchemy").create_engine(engine_url)
        tables = set(inspect(engine).get_table_names())
        engine.dispose()
        missing = EXPECTED_TABLES - tables
        assert not missing, f"Миграции не создали таблицы: {sorted(missing)}"
    finally:
        _drop_database(pg_admin, _MIGRATIONS_DB)


@pytest.mark.parametrize(
    ("index_name", "table"),
    [
        ("uq_dat_crop_cycle_main", "dat_crop_cycle"),  # RULE-AGRO-04
        ("uq_dat_rotation_plan_active", "dat_rotation_plan"),
        ("uq_dat_machine_gov_number", "dat_machine"),
        ("uq_dat_weather_observation", "dat_weather_observation"),
        ("uq_dat_satellite_scene", "dat_satellite_scene"),
    ],
)
def test_unique_indexes_exist(db_engine, index_name: str, table: str):
    indexes = {i["name"] for i in inspect(db_engine).get_indexes(table)}
    assert index_name in indexes, f"нет индекса {index_name} в {table}"
