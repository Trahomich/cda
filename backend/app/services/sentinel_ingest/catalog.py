"""Каталогизация сцен STAC: bbox хозяйства → поиск → пересечение → upsert."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations import SourceUnavailableError
from app.integrations.stac import (
    SATELLITE_SOURCE_ID,
    EarthSearchStacClient,
    StacSearchClient,
    StacItem,
)
from app.models.integrations import SatelliteCatalogItem, SourceJournalEntry
from app.models.references import Source

logger = logging.getLogger(__name__)

#: запас вокруг bbox хозяйства, °: границы полей у края плашки тайла
BBOX_PAD_DEG = 0.02


@dataclass(frozen=True, slots=True)
class CatalogReport:
    """Итог цикла каталогизации (для журналов и тестов)."""

    scenes: int = 0  # item'ов, найденных в каталоге
    fields: int = 0  # активных полей в bbox
    new_rows: int = 0  # новых записей dat_satellite_catalog
    skipped: str | None = None  # причина пропуска цикла
    failed: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failed:
            return "error"
        if self.skipped:
            return "skipped"
        if self.new_rows == 0 and self.scenes == 0:
            return "degraded"  # каталог ответил, сцен за окно нет (данные gap)
        return "ok"


def farm_bbox(db: Session) -> list[float] | None:
    """BBox активных полей хозяйства (WGS84, [minLon, minLat, maxLon, maxLat]).

    contour — geography; extent считается по geometry-представлению.
    """
    row = db.execute(
        text(
            "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
            "FROM (SELECT ST_Extent(contour::geometry) AS e FROM dat_field "
            "WHERE status = 'active') s"
        )
    ).first()
    if row is None or row[0] is None:
        return None
    pad = BBOX_PAD_DEG
    return [float(row[0]) - pad, float(row[1]) - pad, float(row[2]) + pad, float(row[3]) + pad]


def active_fields_count(db: Session) -> int:
    return int(
        db.execute(text("SELECT count(*) FROM dat_field WHERE status = 'active'")).scalar_one()
    )


def intersecting_field_ids(db: Session, geometry: dict) -> list[uuid.UUID]:
    """Активные поля, чей контур пересекает footprint сцены (GeoJSON geometry)."""
    import json

    rows = db.execute(
        text(
            "SELECT id FROM dat_field WHERE status = 'active' "
            "AND ST_Intersects(contour::geometry, "
            "ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326))"
        ),
        {"geom": json.dumps(geometry)},
    ).fetchall()
    return [r[0] for r in rows]


def catalog_upsert(db: Session, field_id: uuid.UUID, item: StacItem) -> bool:
    """Вставка каталог-строки; False, если (field_id, item_id) уже есть.

    rowcount при ON CONFLICT DO NOTHING ненадёжен (драйвер может вернуть 1),
    поэтому факт вставки определяется по RETURNING.
    """
    statement = (
        pg_insert(SatelliteCatalogItem)
        .values(
            item_id=item.item_id,
            field_id=field_id,
            captured_at=item.captured_at,
            tile_id=item.tile_id,
            cloud_pct=item.cloud_pct,
            assets=item.assets,
            status="cataloged",
        )
        .on_conflict_do_nothing(constraint="uq_dat_satellite_catalog")
        .returning(SatelliteCatalogItem.id)
    )
    row = db.execute(statement).first()
    return row is not None


def log_source(db: Session, status: str, reason: str | None) -> None:
    """Журнал состояния источника (API-28): dat_source_journal + ref_source."""
    from datetime import UTC, datetime

    at = datetime.now(tz=UTC)
    db.add(
        SourceJournalEntry(
            source_id=SATELLITE_SOURCE_ID,
            at=at,
            status=status,
            reason=reason[:500] if reason else None,
        )
    )
    db.execute(
        update(Source)
        .where(Source.source_id == SATELLITE_SOURCE_ID)
        .values(
            last_status=status,
            last_success_at=at if status == "ok" else Source.last_success_at,
        )
    )
    db.commit()


def run_catalog_cycle(
    db: Session,
    client: StacSearchClient,
    *,
    date_from: date,
    date_to: date,
) -> CatalogReport:
    """Один цикл каталогизации: поиск за окно → сопоставление полям → upsert.

    Ошибки источника не пробрасываются наружу: пишутся в журнал (планировщик
    повторит цикл); транзакция фиксируется до внешнего вызова.
    """
    bbox = farm_bbox(db)
    if bbox is None:
        report = CatalogReport(skipped="нет активных полей с контурами")
        log_source(db, "degraded", "нет активных полей с контурами")
        return report

    try:
        items = client.search(bbox, date_from, date_to)
    except SourceUnavailableError as exc:
        logger.error("sentinel-ingest: %s", exc)
        log_source(db, "error", str(exc))
        return CatalogReport(failed=[str(exc)])

    db.rollback()  # закрыть неявную транзакцию select'ов перед записью

    new_rows = 0
    touched: set[uuid.UUID] = set()
    for item in items:
        for field_id in intersecting_field_ids(db, item.geometry):
            touched.add(field_id)
            if catalog_upsert(db, field_id, item):
                new_rows += 1
    db.commit()

    report = CatalogReport(scenes=len(items), fields=len(touched), new_rows=new_rows)
    log_source(
        db,
        report.status,
        None if report.status == "ok" else f"сцен за окно {date_from}..{date_to}: {report.scenes}",
    )
    logger.info(
        "sentinel-ingest: каталог %d сцен, полей %d, новых записей %d (%s)",
        report.scenes,
        report.fields,
        report.new_rows,
        report.status,
    )
    return report


__all__ = [
    "CatalogReport",
    "EarthSearchStacClient",
    "farm_bbox",
    "intersecting_field_ids",
    "log_source",
    "run_catalog_cycle",
]
