"""Импорт контуров полей хозяйства из SHP-архива или GeoJSON (IF-FIELDS, v1-02).

Контракт — docs/05-integrations.md §IF-FIELDS и CONTRACTS.md (задача #47):

- source_id = "fields.import" — файловый импорт, не внешний API;
- zip(SHP: shp/shx/dbf/prj/cpg) или .geojson; проекция WGS84 (EPSG:4326);
- полилинии замыкаются (first→last), составные фигуры → MultiPolygon;
- имя поля — атрибут name; natural key идемпотентности — external_id =
  атрибут code (при пустом — name); повторный импорт обновляет геометрию
  и атрибуты, дублей не создаёт;
- площадь: area_ha из атрибута + геодезическая (PostGIS geography, колонка
  area_ga); расхождение > 5 % — строка в area_mismatch отчёта, запись
  сохраняется с площадью из геометрии;
- невалидная геометрия — ST_MakeValid-эквивалент (shapely.make_valid) +
  предупреждение в отчёте; записи без геометрии пропускаются (skipped);
- replace=True — перед импортом удаляются прежние поля хозяйства;
  пользователи и справочники не трогаются.

Запуск: python -m app.cli import-fields <zip|geojson> [--replace]
"""

import json
import logging
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path

import shapefile
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.agronomy import (
    Agrochem,
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    FieldVersion,
    RotationPlan,
    TreatmentPlan,
    YieldRecord,
)
from app.models.auth import AuditRecord
from app.models.integrations import Forecast, SatelliteScene, WeatherObservation
from app.models.machinery import MachineJob

logger = logging.getLogger(__name__)

SOURCE_ID = "fields.import"
AREA_MISMATCH_PCT = 5.0  # порог расхождения «атрибут vs геометрия», % (05 §IF-FIELDS)


@dataclass(slots=True)
class ImportReport:
    """Итог импорта по контракту IF-FIELDS + предупреждения."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    area_mismatch: list[dict] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)

    def as_dict(self) -> dict:
        """Форма отчёта из контракта (warnings — расширение v1-02)."""
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "area_mismatch": self.area_mismatch,
            "warnings": self.warnings,
        }


def _delete_all_fields(db: Session) -> int:
    """replace=True: удалить все поля хозяйства с зависимостями (FK без cascade)."""
    from app.models.machinery import Machine

    field_ids = select(Field.id)
    cycle_ids = select(CropCycle.id).where(CropCycle.field_id.in_(field_ids))
    op_ids = select(FieldOperation.id).where(FieldOperation.field_id.in_(field_ids))
    db.execute(delete(MachineJob).where(MachineJob.field_operation_id.in_(op_ids)))
    db.execute(delete(Alert).where(Alert.field_id.in_(field_ids)))
    db.execute(delete(TreatmentPlan).where(TreatmentPlan.cycle_id.in_(cycle_ids)))
    db.execute(delete(YieldRecord).where(YieldRecord.cycle_id.in_(cycle_ids)))
    db.execute(delete(FieldOperation).where(FieldOperation.id.in_(op_ids)))
    db.execute(delete(CropCycle).where(CropCycle.id.in_(cycle_ids)))
    db.execute(delete(RotationPlan).where(RotationPlan.field_id.in_(field_ids)))
    db.execute(delete(WeatherObservation).where(WeatherObservation.field_id.in_(field_ids)))
    db.execute(delete(Forecast).where(Forecast.field_id.in_(field_ids)))
    db.execute(delete(SatelliteScene).where(SatelliteScene.field_id.in_(field_ids)))
    db.execute(delete(Agrochem).where(Agrochem.field_id.in_(field_ids)))
    db.execute(delete(FieldVersion).where(FieldVersion.field_id.in_(field_ids)))
    removed = len(db.scalars(select(Field.id)).fetchall())
    db.execute(delete(Field))
    db.commit()
    return removed


def _rings_to_geometry(parts: list[int], points: list[tuple]) -> MultiPolygon | None:
    """Полилиния SHP → MultiPolygon: части замыкаются (first→last).

    Каждая часть — самостоятельный контур (составное поле), не «дырка».
    """
    polys: list[Polygon] = []
    bounds = parts + [len(points)]
    for start, end in zip(parts, bounds[1:], strict=False):
        ring = list(points[start:end])
        if len(ring) < 3:
            continue  # вырожденное кольцо (< 3 точек)
        if ring[0] != ring[-1]:
            ring.append(ring[0])  # замыкание POLYLINE → Polygon
        try:
            poly = Polygon(ring)
        except ValueError:
            continue
        if not poly.is_empty and poly.area > 0:
            polys.append(poly)
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _sanitize(geom) -> MultiPolygon | None:
    """Валидация + make_valid; результат — Polygon/MultiPolygon или None."""
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        # make_valid может вернуть коллекцию — оставляем только полигональные части
        geom = unary_union([g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        return geom
    return None


def _read_shp(path: Path) -> list[dict]:
    """zip(SHP) → записи {name, code, area_ha, geom} (pyshp; кодировка из .cpg)."""
    records: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        stem = next(
            (n.rsplit(".", 1)[0] for n in names if n.lower().endswith(".shp")),
            None,
        )
        if stem is None:
            raise ValueError(f"в архиве {path.name} нет .shp-файла")
        encoding = "utf-8"
        for n in names:
            if n.lower().endswith(".cpg"):
                encoding = zf.read(n).decode("ascii", errors="ignore").strip() or "utf-8"
        prj = next((n for n in names if n.lower().endswith(".prj")), None)
        if prj is not None and b"4326" not in zf.read(prj) and b"WGS_1984" not in zf.read(prj):
            raise ValueError(
                f"проекция {path.name} не WGS84 (EPSG:4326): перепроектирование "
                "при импорте не поддерживается — сконвертируйте файл"
            )
        with tempfile.TemporaryDirectory() as tmp:
            zf.extractall(tmp)
            reader = shapefile.Reader(
                shp=str(Path(tmp) / f"{stem}.shp"),
                shx=str(Path(tmp) / f"{stem}.shx"),
                dbf=str(Path(tmp) / f"{stem}.dbf"),
                encoding=encoding,
            )
            fields = [f[0] for f in reader.fields[1:]]
            for i in range(len(reader)):
                attrs = dict(zip(fields, reader.record(i), strict=False))
                shp = reader.shape(i)
                geom = None
                if shp.shapeType == shapefile.POLYLINE or shp.shapeType == shapefile.POLYGON:
                    geom = _rings_to_geometry(list(shp.parts), list(shp.points))
                records.append(
                    {
                        "name": (attrs.get("name") or "").strip() or f"field-{attrs.get('id', i)}",
                        "code": (attrs.get("code") or "").strip(),
                        "area_ha": attrs.get("area_ha"),
                        "geom": geom,
                    }
                )
    return records


def _read_geojson(path: Path) -> list[dict]:
    """GeoJSON FeatureCollection → записи {name, code, area_ha, geom}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features", [data] if data.get("type") == "Feature" else [])
    records: list[dict] = []
    for feat in features:
        props = feat.get("properties") or {}
        g = feat.get("geometry")
        geom = shape(g) if g and g.get("type") in ("Polygon", "MultiPolygon") else None
        records.append(
            {
                "name": (props.get("name") or "").strip() or f"field-{len(records)}",
                "code": (props.get("code") or "").strip(),
                "area_ha": props.get("area_ha"),
                "geom": geom,
            }
        )
    return records


def import_fields(db: Session, payload_ref: str, *, replace: bool = False) -> ImportReport:
    """Импорт полей хозяйства (IF-FIELDS). Идемпотентен по external_id."""
    report = ImportReport()
    path = Path(payload_ref)
    if not path.exists():
        raise FileNotFoundError(f"файл импорта не найден: {payload_ref}")

    if replace:
        removed = _delete_all_fields(db)
        report.warnings.append(f"replace: удалено прежних полей хозяйства — {removed}")

    records = _read_geojson(path) if path.suffix.lower() == ".geojson" else _read_shp(path)

    existing = {
        f.external_id: f for f in db.scalars(select(Field).where(Field.external_id.isnot(None)))
    }
    seen_external: set[str] = set()
    # (field, attr_ha): пары «сохранённое поле ↔ атрибут area_ha из файла»
    touched: list[tuple[Field, float | None]] = []
    for rec in records:
        if rec["geom"] is None:
            report.skipped += 1  # запись без геометрии — по контракту пропускается
            continue
        geom = _sanitize(rec["geom"])
        if geom is None:
            report.skipped += 1
            report.warnings.append(f"«{rec['name']}»: геометрия не восстановлена, пропущено")
            continue
        if not geom.is_valid:
            report.warnings.append(f"«{rec['name']}»: геометрия исправлена (make_valid)")

        external_id = rec["code"] or rec["name"]
        if external_id in seen_external:
            # имена в реальном SHP повторяются — ключ дополняется id записи
            suffix = rec["code"] or str(len(seen_external) + 1)
            new_id = f"{external_id}#{suffix}"[:100]
            report.warnings.append(
                f"«{rec['name']}»: коллизия external_id «{external_id}» → «{new_id}»"
            )
            external_id = new_id
        seen_external.add(external_id)

        wkt = f"SRID=4326;{geom.wkt}"
        field_ = existing.get(external_id)
        if field_ is None:
            field_ = Field(name=rec["name"], contour=wkt, external_id=external_id)
            db.add(field_)
            db.flush()
            version = FieldVersion(
                field_id=field_.id,
                version_no=1,
                contour=wkt,
                valid_from=date.today(),
                reason=f"импорт полей ({path.name}, {SOURCE_ID})",
            )
            db.add(version)
            db.flush()
            field_.current_version_id = version.id
            report.created += 1
        else:
            field_.name = rec["name"]
            field_.contour = wkt
            max_no = db.scalar(
                select(FieldVersion.version_no)
                .where(FieldVersion.field_id == field_.id)
                .order_by(FieldVersion.version_no.desc())
                .limit(1)
            )
            version = FieldVersion(
                field_id=field_.id,
                version_no=(max_no or 0) + 1,
                contour=wkt,
                valid_from=date.today(),
                reason=f"повторный импорт полей ({path.name}, {SOURCE_ID})",
            )
            db.add(version)
            db.flush()
            field_.current_version_id = version.id
            report.updated += 1
        touched.append(field_)

    db.flush()

    # расхождения площадей: атрибут area_ha vs геодезическая (area_ga из БД)
    db.expire_all()  # перечитать вычисленную area_ga
    for field_ in touched:
        attr_ha = rec_area = None
        raise_not_found = False
        for rec in records:
            if rec["geom"] is not None and rec["name"] == field_.name:
                rec_area = rec["area_ha"]
                break
        attr_ha = rec_area
        if attr_ha is None:
            continue
        attr_ha = float(attr_ha)
        calc_ha = float(field_.area_ga)
        if attr_ha > 0 and abs(calc_ha - attr_ha) / attr_ha * 100 > AREA_MISMATCH_PCT:
            report.area_mismatch.append(
                {
                    "name": field_.name,
                    "attr_ha": round(attr_ha, 2),
                    "calc_ha": round(calc_ha, 2),
                }
            )

    db.add(
        AuditRecord(
            entity="field",
            entity_id=uuid.uuid4(),  # NOT NULL; сводная запись импорта
            user_id=None,
            action="import",
            changed={
                "source_id": SOURCE_ID,
                "file": path.name,
                "created": report.created,
                "updated": report.updated,
                "skipped": report.skipped,
                "area_mismatch": len(report.area_mismatch),
            },
        )
    )
    db.commit()
    logger.info(
        "импорт полей %s: создано %d, обновлено %d, пропущено %d, расхождений площади %d",
        path.name,
        report.created,
        report.updated,
        report.skipped,
        len(report.area_mismatch),
    )
    return report


__all__ = ["ImportReport", "import_fields", "mapping", "SOURCE_ID"]
