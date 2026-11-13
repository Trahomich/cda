"""Погода: почва (T/влажность), ET0, УФ, точка росы, давление (#193).

Nullable-колонки в dat_weather_observation и dat_forecast: почва посевного
слоя (T °C, влажность % объёмной), ET0 мм/сут, УФ-индекс, точка росы °C,
давление на уровне моря гПа. Провайдер может не отдавать — null.

Revision ID: 0023_weather_soil_et0
Revises: 0022_unified_operation
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0023_weather_soil_et0"
down_revision = "0022_unified_operation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = [
    ("soil_temperature_c", sa.Numeric(4, 1)),
    ("soil_moisture_pct", sa.Numeric(5, 1)),
    ("et0_mm", sa.Numeric(5, 1)),
    ("uv_index", sa.Numeric(3, 1)),
    ("dew_point_c", sa.Numeric(4, 1)),
    ("pressure_hpa", sa.Numeric(6, 1)),
]


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("dat_weather_observation", sa.Column(name, type_, nullable=True))
    for name, type_ in _COLUMNS:
        # в прогнозе температура почвы — короткое имя API (§7.8 #193)
        fname = "soil_temp_c" if name == "soil_temperature_c" else name
        op.add_column("dat_forecast", sa.Column(fname, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        fname = "soil_temp_c" if name == "soil_temperature_c" else name
        op.drop_column("dat_forecast", fname)
    for name, _ in reversed(_COLUMNS):
        op.drop_column("dat_weather_observation", name)
