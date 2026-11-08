"""Таблица ai_field_advice: AI-рекомендации «что делать сейчас» (09 §9.3, #119).

Хранение рекомендаций эпика #118: одна строка — результат генерации для поля
(модель, провайдер, advice_date — дата «настоящего момента», answer,
context_hash для идемпотентности пересчёта, status ok/fallback/empty —
empty не пишем, констрейнт только валидирует словарь статусов).

Revision ID: 0018_ai_field_advice
Revises: 0017_culture_yield_coeffs
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0018_ai_field_advice"
down_revision = "0017_culture_yield_coeffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_field_advice",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_ai_field_advice_field"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("advice_date", sa.Date(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="ok", nullable=False),
        sa.CheckConstraint("status IN ('ok', 'fallback', 'empty')",
                           name="ck_ai_field_advice_status"),
        sa.CheckConstraint("provider IN ('anthropic', 'ollama')",
                           name="ck_ai_field_advice_provider"),
    )
    op.create_index(
        "ix_ai_field_advice_field_created", "ai_field_advice", ["field_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_field_advice_field_created", table_name="ai_field_advice")
    op.drop_table("ai_field_advice")
