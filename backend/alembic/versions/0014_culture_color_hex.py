"""color_hex в справочнике культур (v1-08, задача #54; замечание №2, сообщение #148).

ref_culture.color_hex varchar(7) NULL — стабильный контрастный цвет культуры
для раскраски полей на карте Leaflet (фронт #55); nullable: пользовательские
культуры могут быть без цвета. Значения наполняет сид (app/seed.py),
существующие строки актуализируются через ON CONFLICT DO UPDATE.

Revision ID: 0014_culture_color_hex
Revises: 0013_field_import
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0014_culture_color_hex"
down_revision = "0013_field_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ref_culture",
        sa.Column("color_hex", sa.String(length=7), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ref_culture", "color_hex")
