"""Импорт реальных полей (v1-02, задача #48): external_id и MultiPolygon.

- dat_field.external_id — natural key идемпотентности импорта (атрибут code,
  при пустом — name; docs/05 §IF-FIELDS); уникален, NULL разрешён (много).
- dat_field.contour / dat_field_version.contour: geography(POLYGON) →
  geography(MULTIPOLYGON): в реальном SHP 31 составная фигура.
- uq_dat_field_name снят: в реальном файле имена повторяются (6 дублей на 90
  полей); защиту API-02/03 от дублей имён обеспечивает service-проверка (409
  duplicate_name), уникальность записей — по external_id.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_field_import"
down_revision = "0011_operation_needs_review"
branch_labels = None
depends_on = None

_NEW_TYPE = "geography(MULTIPOLYGON,4326)"
_OLD_TYPE = "geography(POLYGON,4326)"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE dat_field "
        "ALTER COLUMN contour TYPE geography(MULTIPOLYGON,4326) "
        "USING ST_Multi(contour::geometry)::geography(MULTIPOLYGON,4326)"
    )
    op.execute(
        "ALTER TABLE dat_field_version "
        "ALTER COLUMN contour TYPE geography(MULTIPOLYGON,4326) "
        "USING ST_Multi(contour::geometry)::geography(MULTIPOLYGON,4326)"
    )
    op.add_column("dat_field", sa.Column("external_id", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_dat_field_external_id", "dat_field", ["external_id"])
    op.drop_constraint("uq_dat_field_name", "dat_field", type_="unique")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE dat_field_version "
        "ALTER COLUMN contour TYPE geography(POLYGON,4326) "
        "USING ST_GeometryN(contour::geometry, 1)::geography(POLYGON,4326)"
    )
    op.execute(
        "ALTER TABLE dat_field "
        "ALTER COLUMN contour TYPE geography(POLYGON,4326) "
        "USING ST_GeometryN(contour::geometry, 1)::geography(POLYGON,4326)"
    )
    op.create_unique_constraint("uq_dat_field_name", "dat_field", ["name"])
    op.drop_constraint("uq_dat_field_external_id", "dat_field", type_="unique")
    op.drop_column("dat_field", "external_id")
