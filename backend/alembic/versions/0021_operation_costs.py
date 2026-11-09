"""Стоимость техработ: колонки dat_field_operation + справочник нормативов
(§7.9, #161). cost_mode 'unit' — руб/га (× площадь операции), 'total' —
руб за поле; NULL — стоимости нет (факт без стоимости берёт норматив
ref_operation_cost по типу операции).

Revision ID: 0021_operation_costs
Revises: 0020_scene_indices
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision = "0021_operation_costs"
down_revision = "0020_scene_indices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ориентировочные расценки техработ, руб/га (регион; уточняются хозяйством)
OPERATION_COSTS: dict[str, float] = {
    "spraying": 350.0,
    "fertilizing": 300.0,
    "seeding": 900.0,
    "harvesting": 1200.0,
    "tillage": 700.0,
    "other": 500.0,
}


def upgrade() -> None:
    op.add_column(
        "dat_field_operation", sa.Column("cost_mode", sa.String(8), nullable=True)
    )
    op.add_column(
        "dat_field_operation", sa.Column("cost_value", sa.Numeric(12, 2), nullable=True)
    )
    op.create_check_constraint(
        "ck_dat_field_operation_cost_mode",
        "dat_field_operation",
        "cost_mode IN ('unit', 'total') OR cost_mode IS NULL",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_cost",
        "dat_field_operation",
        "cost_value IS NULL OR cost_value >= 0",
    )
    op.create_table(
        "ref_operation_cost",
        sa.Column("operation_type", sa.String(15), primary_key=True),
        sa.Column("rub_per_ga", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rub_per_ga >= 0", name="ck_ref_operation_cost_rub"),
    )
    # seed идемпотентен: существующие нормативы хозяйства не перезаписываем
    op.execute(
        pg_insert(sa.table("ref_operation_cost"))
        .values(
            [
                {"operation_type": t, "rub_per_ga": v}
                for t, v in OPERATION_COSTS.items()
            ]
        )
        .on_conflict_do_nothing(index_elements=["operation_type"])
    )


def downgrade() -> None:
    op.drop_table("ref_operation_cost")
    op.drop_constraint("ck_dat_field_operation_cost", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_cost_mode", "dat_field_operation")
    op.drop_column("dat_field_operation", "cost_value")
    op.drop_column("dat_field_operation", "cost_mode")
