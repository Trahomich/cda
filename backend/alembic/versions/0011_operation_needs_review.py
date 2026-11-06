"""needs_review в журнале операций (RULE-AGRO-12, доработка #27, задача MVP-9a).

dat_field_operation.needs_review boolean NOT NULL default false: превышение
фактической площади над площадью поля (actual_area_ga > 1,05 × area_ga
по последней версии контура) не блокирует запись — операция сохраняется
с пометкой «требует проверки» (03 §3.3/§3.4, контракт docs/06 API-17,
ред. 5afd553).

Revision ID: 0011_operation_needs_review
Revises: 0010_operations_idem
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0011_operation_needs_review"
down_revision = "0010_operations_idem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dat_field_operation",
        sa.Column(
            "needs_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("dat_field_operation", "needs_review")
