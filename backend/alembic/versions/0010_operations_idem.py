"""Журнал операций: идемпотентность и связи (задача MVP-9, API-17/18).

В dat_field_operation добавляются:
- client_id (uuid, nullable) + partial unique index — идемпотентность
  офлайн-очереди PWA (10-mobile-pwa §3, API-17): повторный POST с тем же
  client_id возвращает существующую операцию без дубля;
- machine_job_id (uuid, nullable, FK dat_machine_job.id) — связь факта
  операции с работой техники (docs/04, обратная связь field_operation_id);
- deviation_rationale (строка 500) — обоснование отклонения план/факт
  (RULE-AGRO-11, docs/03 §3.4).

Revision ID: 0010_operations_idem
Revises: 0009_rotation_rules
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0010_operations_idem"
down_revision = "0009_rotation_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dat_field_operation",
        sa.Column("client_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "dat_field_operation",
        sa.Column(
            "machine_job_id",
            sa.Uuid(),
            sa.ForeignKey("dat_machine_job.id", name="fk_dat_field_operation_job"),
            nullable=True,
        ),
    )
    op.add_column(
        "dat_field_operation",
        sa.Column("deviation_rationale", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "uq_dat_field_operation_client_id",
        "dat_field_operation",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_dat_field_operation_client_id", table_name="dat_field_operation")
    op.drop_column("dat_field_operation", "deviation_rationale")
    op.drop_column("dat_field_operation", "machine_job_id")
    op.drop_column("dat_field_operation", "client_id")
