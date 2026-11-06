"""Алерты: ручные (custom) алерты (задача MVP-12, API-27).

ENT-ALERT (docs/03 §1.8): типы weather_danger / ndvi_anomaly / deviation /
machine_service / data_source заданы CHECK-ограничением миграции 0004.
Для ручных алертов пользователя (POST /alerts, MVP-12) добавляется тип
'custom' — расширение CHECK, не ломающее существующие строки.

Revision ID: 0011_alert_custom
Revises: 0010_operations_idem
Create Date: 2026-11-06
"""

from collections.abc import Sequence

from alembic import op

revision = "0011_alert_custom"
down_revision = "0010_operations_idem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TYPES = "('weather_danger', 'ndvi_anomaly', 'deviation', 'machine_service', 'data_source')"
_NEW_TYPES = "('weather_danger', 'ndvi_anomaly', 'deviation', 'machine_service', 'data_source', 'custom')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE dat_alert DROP CONSTRAINT ck_dat_alert_type")
    op.create_check_constraint(
        "ck_dat_alert_type",
        "dat_alert",
        f"type IN {_NEW_TYPES}",
    )


def downgrade() -> None:
    # тип custom несовместим со старым ограничением — удаляем такие алерты
    op.execute("DELETE FROM dat_alert WHERE type = 'custom'")
    op.execute("ALTER TABLE dat_alert DROP CONSTRAINT ck_dat_alert_type")
    op.create_check_constraint(
        "ck_dat_alert_type",
        "dat_alert",
        f"type IN {_OLD_TYPES}",
    )
