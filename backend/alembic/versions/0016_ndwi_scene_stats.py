"""NDWI при сцене NDVI: детектор вымоканий (v3-01, #91; docs/06 §7.4).

Вместе со статистикой NDVI сцены считается NDWI = (B03 − B08)/(B03 + B08)
по тем же пикселям (маска SCL, окно по контуру); выжимки пишутся колонками
ndwi_mean, ndwi_p90 в dat_satellite_scene — таблицу статистики сцен
(«dat_scene_stats» в терминах docs/06 §7.4; отдельной таблицы нет, статистика
индексов хранится в dat_satellite_scene, 06 §2). Старые строки — NULL
(канал b03 появился в активах каталога с #83) — это не ошибка.

Правило вымокания (§7.4): ndwi_p90 ≥ 0.10 И ndvi_mean < 0.45 И осадки за
7 суток по полю ≥ 20 мм (dat_weather_observation, IF-WEATHER); API-06
отдаёт waterlogged: bool | null (null — данных нет).

Revision ID: 0016_ndwi_scene_stats
Revises: 0015_forecast_precip_prob
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0016_ndwi_scene_stats"
down_revision = "0015_forecast_precip_prob"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dat_satellite_scene",
        sa.Column("ndwi_mean", sa.Numeric(4, 3), nullable=True),
    )
    op.add_column(
        "dat_satellite_scene",
        sa.Column("ndwi_p90", sa.Numeric(4, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dat_satellite_scene", "ndwi_p90")
    op.drop_column("dat_satellite_scene", "ndwi_mean")
