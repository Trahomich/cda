"""Каталог сцен Sentinel-2 из STAC earth-search (v1-04, #50; docs/05 §2, ADR-09).

dat_satellite_catalog: метаданные найденных сцен L2A до вычисления индексов —
item_id STAC, tile (MGRS), cloud_cover, ссылки на активы COG (b04/b08/scl).
Статус cataloged → processed/failed обновляет этап расчёта NDVI (#51);
рассчитанные индексы пишутся в dat_satellite_scene по правилам 05 §5.
Дедупликация UQ(field_id, item_id); cloud_pct ≤ 60 (05 §5 п.2).

Revision ID: 0012_satellite_catalog
Revises: 0011_operation_needs_review
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012_satellite_catalog"
down_revision = "0011_operation_needs_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dat_satellite_catalog",
        sa.Column("id", UUID(), nullable=False),
        sa.Column("item_id", sa.String(length=120), nullable=False),
        sa.Column(
            "field_id",
            UUID(),
            sa.ForeignKey("dat_field.id", name="fk_dat_satellite_catalog_field"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.Date(), nullable=False),
        sa.Column("tile_id", sa.String(length=20), nullable=False),
        sa.Column("cloud_pct", sa.Numeric(4, 1), nullable=False),
        sa.Column("assets", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="cataloged", nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dat_satellite_catalog"),
        sa.UniqueConstraint("field_id", "item_id", name="uq_dat_satellite_catalog"),
        sa.CheckConstraint(
            "status IN ('cataloged', 'processed', 'failed')",
            name="ck_dat_satellite_catalog_status",
        ),
        sa.CheckConstraint("cloud_pct <= 60", name="ck_dat_satellite_catalog_cloud"),
        sa.CheckConstraint("cloud_pct >= 0", name="ck_dat_satellite_catalog_cloud_pos"),
    )
    op.create_index(
        "ix_dat_satellite_catalog_status",
        "dat_satellite_catalog",
        ["status", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dat_satellite_catalog_status", table_name="dat_satellite_catalog")
    op.drop_table("dat_satellite_catalog")
