"""integrations: погода, спутник, журнал источников (задача MVP-2).

docs/06-data-api.md §2 + нормализованные структуры docs/05-integrations.md §3.
source_id — строковый ключ адаптера, FK → ref_source(source_id).
Дедупликация: UQ(field_id, observed_at, source_id) для погоды,
UQ(field_id, index, captured_at, source_id) для сцен (05 §4);
сцены с cloud_pct > 60 не сохраняются вовсе (05 §5) — CHECK <= 60.

Revision ID: 0006_integrations
Revises: 0005_machinery
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_integrations"
down_revision = "0005_machinery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Факты погоды (сутки × поле): дедупликация по UQ(field_id, observed_at, source_id)
    op.create_table(
        "dat_weather_observation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_weather_observation_field"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.Date(), nullable=False),
        sa.Column("temperature_avg_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("temperature_min_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("temperature_max_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("precipitation_mm", sa.Numeric(5, 1), nullable=True),
        sa.Column("wind_max_ms", sa.Numeric(4, 1), nullable=True),
        sa.Column("humidity_avg_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("trust", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ref_source.source_id"], name="fk_dat_weather_observation_source"
        ),
        sa.UniqueConstraint(
            "field_id", "observed_at", "source_id", name="uq_dat_weather_observation"
        ),
    )

    # Прогноз: последняя версия на горизонт 7 сут, перезаписывается (06 §4)
    op.create_table(
        "dat_forecast",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_forecast_field"),
            nullable=False,
        ),
        sa.Column("forecast_for", sa.Date(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_min_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("temperature_max_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("precipitation_mm", sa.Numeric(5, 1), nullable=True),
        sa.Column("wind_max_ms", sa.Numeric(4, 1), nullable=True),
        sa.Column("humidity_avg_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column(
            "hazard",
            sa.String(length=15),
            sa.CheckConstraint(
                "hazard IN ('none', 'frost', 'heavy_rain', 'hail', 'wind')",
                name="ck_dat_forecast_hazard",
            ),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("field_id", "forecast_for", name="uq_dat_forecast"),
    )

    # Спутниковые сцены: облачность > 60 % не сохраняется (05 §5)
    op.create_table(
        "dat_satellite_scene",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_satellite_scene_field"),
            nullable=False,
        ),
        sa.Column(
            "index",
            sa.String(length=5),
            sa.CheckConstraint(
                '"index" IN (\'ndvi\', \'ndre\', \'ndmi\', \'savi\')',
                name="ck_dat_satellite_scene_index",
            ),
            nullable=False,
        ),
        sa.Column("captured_at", sa.Date(), nullable=False),
        sa.Column("resolution_m", sa.Integer(), nullable=False),
        sa.Column("index_avg", sa.Numeric(4, 3), nullable=False),
        sa.Column("index_min", sa.Numeric(4, 3), nullable=False),
        sa.Column("index_max", sa.Numeric(4, 3), nullable=False),
        sa.Column("anomaly_zones_ref", sa.String(length=500), nullable=True),
        sa.Column("cloud_pct", sa.Numeric(4, 1), nullable=False),
        sa.CheckConstraint("cloud_pct <= 60", name="ck_dat_satellite_scene_cloud"),
        sa.Column("composite_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("trust", sa.Numeric(3, 2), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ref_source.source_id"], name="fk_dat_satellite_scene_source"
        ),
        sa.UniqueConstraint(
            "field_id", "index", "captured_at", "source_id", name="uq_dat_satellite_scene"
        ),
    )
    op.create_index(
        "ix_dat_satellite_scene_field_date", "dat_satellite_scene", ["field_id", "captured_at"]
    )

    # Журнал состояний адаптеров (REQ-F-034): события ok/degraded/error + причина
    op.create_table(
        "dat_source_journal",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=10),
            sa.CheckConstraint(
                "status IN ('ok', 'degraded', 'error')", name="ck_dat_source_journal_status"
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ref_source.source_id"], name="fk_dat_source_journal_source"
        ),
    )
    op.create_index("ix_dat_source_journal_source_at", "dat_source_journal", ["source_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_dat_source_journal_source_at", table_name="dat_source_journal")
    op.drop_table("dat_source_journal")
    op.drop_index("ix_dat_satellite_scene_field_date", table_name="dat_satellite_scene")
    op.drop_table("dat_satellite_scene")
    op.drop_table("dat_forecast")
    op.drop_table("dat_weather_observation")
