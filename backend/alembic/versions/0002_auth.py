"""auth: таблицы dat_user и dat_audit (задача MVP-4).

dat_user — пользователи (роли по 01-requirements §2), пароли argon2id.
dat_audit — аудит критичных действий (06-data-api §4); для MVP-4 действие
расширено значениями login / login_failed (аудит входов).

Revision ID: 0002_auth
Revises: 0001_init
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_auth"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dat_user",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fio", sa.String(length=200), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.String(length=40),
            sa.CheckConstraint(
                "role IN ('admin','agronomist','manager','machine_operator',"
                "'livestock_specialist')",
                name="ck_dat_user_role",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("login", name="uq_dat_user_login"),
    )
    op.create_index("ix_dat_user_login", "dat_user", ["login"])

    op.create_table(
        "dat_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entity", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("dat_user.id"), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("changed", sa.JSON(), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_dat_audit_entity", "dat_audit", ["entity"])
    op.create_index("ix_dat_audit_at", "dat_audit", ["at"])


def downgrade() -> None:
    op.drop_index("ix_dat_audit_at", table_name="dat_audit")
    op.drop_index("ix_dat_audit_entity", table_name="dat_audit")
    op.drop_table("dat_audit")
    op.drop_index("ix_dat_user_login", table_name="dat_user")
    op.drop_table("dat_user")
