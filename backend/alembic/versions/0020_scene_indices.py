"""dat_satellite_scene: расширение списка индексов (§7.7, #128).

Check-constraint ck_dat_satellite_scene_index: добавлены ndwi, ndsi, evi,
bsi к прежним ndvi/ndre/ndmi/savi (индексы §7.7 пишутся отдельными сценами
по UQ(field_id, index, captured_at, source_id)).

Revision ID: 0020_scene_indices
Revises: 0019_weather_cloud_snow
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0020_scene_indices"
down_revision = "0019_weather_cloud_snow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_CONSTRAINT = (
    "\"index\" IN ('ndvi', 'ndwi', 'ndre', 'ndmi', 'ndsi', 'evi', 'savi', 'bsi')"
)
_OLD_CONSTRAINT = "\"index\" IN ('ndvi', 'ndre', 'ndmi', 'savi')"


def upgrade() -> None:
    op.drop_constraint("ck_dat_satellite_scene_index", "dat_satellite_scene")
    op.create_check_constraint(
        "ck_dat_satellite_scene_index",
        "dat_satellite_scene",
        _NEW_CONSTRAINT,
    )


def downgrade() -> None:
    # сцены новых индексов удаляются: констрейнт прежний их не пропускает
    op.execute(
        sa.text(
            "DELETE FROM dat_satellite_scene "
            "WHERE \"index\" IN ('ndwi', 'ndsi', 'evi', 'bsi')"
        )
    )
    op.drop_constraint("ck_dat_satellite_scene_index", "dat_satellite_scene")
    op.create_check_constraint(
        "ck_dat_satellite_scene_index",
        "dat_satellite_scene",
        _OLD_CONSTRAINT,
    )
