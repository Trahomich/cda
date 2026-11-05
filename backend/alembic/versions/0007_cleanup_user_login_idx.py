"""cleanup: дроп дублирующего индекса ix_dat_user_login (задача MVP-2).

0002_auth создал UniqueConstraint uq_dat_user_login (уже даёт индекс)
и поверх — неуникальный ix_dat_user_login; модель приведена к миграции
(решение в обсуждении #20, пост #73): лишний индекс удаляем.

Revision ID: 0007_cleanup_user_login_idx
Revises: 0006_integrations
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision = "0007_cleanup_user_login_idx"
down_revision = "0006_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_dat_user_login", table_name="dat_user")


def downgrade() -> None:
    op.create_index("ix_dat_user_login", "dat_user", ["login"])
