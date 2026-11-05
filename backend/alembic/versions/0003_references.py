"""references: справочники ref_ (задача MVP-2).

Справочники по docs/06-data-api.md §3: культуры, матрица совместимости
предшественников (RULE-AGRO-01), источники данных, параметры погодных окон
(RULE-AGRO-09), результаты агрохимии, цены. FK ref_agrochem.field_id →
dat_field добавляется миграцией 0004 (поле создаётся позже).

Revision ID: 0003_references
Revises: 0002_auth
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_references"
down_revision = "0002_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Культуры: kind — роль в севообороте (main/forage/catch), для кормового баланса SC-05
    op.create_table(
        "ref_culture",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=10),
            sa.CheckConstraint(
                "kind IN ('main', 'forage', 'catch')", name="ck_ref_culture_kind"
            ),
            nullable=False,
        ),
        sa.Column("forage_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("crop_group", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_ref_culture_name"),
    )

    # Матрица совместимости «предшественник → культура» (RULE-AGRO-01, REQ-F-006)
    op.create_table(
        "ref_predecessor_matrix",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "culture_id",
            sa.Uuid(),
            sa.ForeignKey("ref_culture.id", name="fk_ref_pred_matrix_culture"),
            nullable=False,
        ),
        sa.Column(
            "predecessor_id",
            sa.Uuid(),
            sa.ForeignKey("ref_culture.id", name="fk_ref_pred_matrix_predecessor"),
            nullable=False,
        ),
        sa.Column(
            "verdict",
            sa.String(length=10),
            sa.CheckConstraint(
                "verdict IN ('ok', 'warn', 'ban')", name="ck_ref_pred_matrix_verdict"
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("culture_id", "predecessor_id", name="uq_ref_pred_matrix_pair"),
    )

    # Адаптеры источников данных (05 §6, REQ-F-034): текущий статус — здесь,
    # история событий — в dat_source_journal (миграция 0006)
    op.create_table(
        "ref_source",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "last_status",
            sa.String(length=20),
            sa.CheckConstraint(
                "last_status IN ('ok', 'degraded', 'error') OR last_status IS NULL",
                name="ck_ref_source_last_status",
            ),
            nullable=True,
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source_id", name="uq_ref_source_source_id"),
    )

    # Параметры погодных окон по типу операции (RULE-AGRO-09): пороги редактируемы,
    # текст правила хранится рядом — для объяснимости (REQ-NF-006, 03 §6.4)
    op.create_table(
        "ref_weather_rule",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("rule_text", sa.String(length=1000), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("operation_type", name="uq_ref_weather_rule_op_type"),
    )

    # Результаты агрохимии (REQ-F-004, 031): мг/100 г, слой 0–30 см
    op.create_table(
        "ref_agrochem",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("sample_date", sa.Date(), nullable=False),
        sa.Column("ph", sa.Numeric(4, 1), nullable=True),
        sa.Column("humus_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("n_mg100", sa.Numeric(6, 2), nullable=True),
        sa.Column("p2o5_mg100", sa.Numeric(6, 2), nullable=True),
        sa.Column("k2o_mg100", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ref_agrochem_field_date", "ref_agrochem", ["field_id", "sample_date"])

    # Цены (REQ-F-033): item_type = crop | input (feed/product — пост-MVP, 12-economics §3.3)
    op.create_table(
        "ref_price",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_type",
            sa.String(length=10),
            sa.CheckConstraint("item_type IN ('crop', 'input')", name="ck_ref_price_item_type"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("price_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ref_price_item_date", "ref_price", ["item_type", "item_id", "date"]
    )


def downgrade() -> None:
    op.drop_index("ix_ref_price_item_date", table_name="ref_price")
    op.drop_table("ref_price")
    op.drop_index("ix_ref_agrochem_field_date", table_name="ref_agrochem")
    op.drop_table("ref_weather_rule")
    op.drop_table("ref_source")
    op.drop_table("ref_predecessor_matrix")
    op.drop_table("ref_culture")
