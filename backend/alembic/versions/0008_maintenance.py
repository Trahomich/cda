"""dat_maintenance — ТО и ремонты (задача MVP-7, docs/04-domain-farm.md §1).

В 0005_machinery вошли только dat_machine / dat_machine_job; таблица
ENT-MAINTENANCE добавляется сейчас для API-21 (GET/POST /maintenance).
Алерты RULE-TECH-02/05 — вне объёма MVP-7.

Revision ID: 0008_maintenance
Revises: 0007_cleanup_user_login_idx
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0008_maintenance"
down_revision = "0007_cleanup_user_login_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dat_maintenance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("machine_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("due_at_hours", sa.Numeric(10, 1), nullable=False),
        sa.Column("done_at", sa.Date(), nullable=True),
        sa.Column("done_at_hours", sa.Numeric(10, 1), nullable=True),
        sa.Column("downtime_h", sa.Numeric(6, 1), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('service', 'repair', 'inspection')", name="ck_dat_maintenance_kind"
        ),
        sa.CheckConstraint("due_at_hours >= 0", name="ck_dat_maintenance_due_hours"),
        sa.CheckConstraint("done_at_hours >= 0", name="ck_dat_maintenance_done_hours"),
        sa.CheckConstraint("downtime_h >= 0", name="ck_dat_maintenance_downtime"),
        sa.ForeignKeyConstraint(
            ["machine_id"], ["dat_machine.id"], name="fk_dat_maintenance_machine"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dat_maintenance_machine_due", "dat_maintenance", ["machine_id", "due_at_hours"]
    )


def downgrade() -> None:
    op.drop_index("ix_dat_maintenance_machine_due", table_name="dat_maintenance")
    op.drop_table("dat_maintenance")
