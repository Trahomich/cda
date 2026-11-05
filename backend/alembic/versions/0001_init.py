"""init: пустая первая миграция (создание каркаса без объектов БД).

PostGIS-расширение создаётся в docker-entrypoint-initdb.d (инит БД),
потому что в контейнере postgis/postgis оно доступно до первого старта API.
Здесь миграция оставлена пустой: первые доменные таблицы добавят задачи MVP-2+.

Revision ID: 0001_init
Revises:
Create Date: 2026-11-05
"""

from typing import Sequence, Union

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
