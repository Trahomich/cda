"""precip_prob_pct в прогнозе погоды (v2-03, задача #73; docs/06 §7.2).

dat_forecast.precip_prob_pct smallint NULL — вероятность осадков, %
(Open-Meteo precipitation_probability_max). Nullable: провайдер может не
отдавать вероятность (контракт §7.2: новые потребители tolerate null).

Revision ID: 0015_forecast_precip_prob
Revises: 0014_culture_color_hex
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0015_forecast_precip_prob"
down_revision = "0014_culture_color_hex"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dat_forecast",
        sa.Column("precip_prob_pct", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dat_forecast", "precip_prob_pct")
