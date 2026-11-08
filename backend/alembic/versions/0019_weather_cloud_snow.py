"""dat_weather_observation: облачность/снег/часы осадков (#126).

Новые метрики истории погоды для карточки погоды и детекторов прошлых
сезонов: cloud_cover_mean_pct (%), snowfall_cm и snow_depth_cm (см),
precipitation_hours (ч). Все nullable — источник может не отдавать.

Revision ID: 0019_weather_cloud_snow
Revises: 0018_ai_field_advice
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0019_weather_cloud_snow"
down_revision = "0018_ai_field_advice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dat_weather_observation",
        sa.Column("cloud_cover_mean_pct", sa.Numeric(4, 1), nullable=True),
    )
    op.add_column(
        "dat_weather_observation",
        sa.Column("snowfall_cm", sa.Numeric(5, 1), nullable=True),
    )
    op.add_column(
        "dat_weather_observation",
        sa.Column("snow_depth_cm", sa.Numeric(5, 1), nullable=True),
    )
    op.add_column(
        "dat_weather_observation",
        sa.Column("precipitation_hours", sa.Numeric(3, 1), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dat_weather_observation", "precipitation_hours")
    op.drop_column("dat_weather_observation", "snow_depth_cm")
    op.drop_column("dat_weather_observation", "snowfall_cm")
    op.drop_column("dat_weather_observation", "cloud_cover_mean_pct")
