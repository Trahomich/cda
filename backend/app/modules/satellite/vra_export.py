"""Экспорт карты зон дифференцированного внесения (API-57, docs/06 §7.4, #125).

Три формата: ISOXML (ISO 11783-10 TASKDATA, бортовые терминалы), Shapefile
(ГИС/агроконсультанты) и GeoJSON. Дозы — из последнего approved fertilizing-
плана (kg_ha) или явным параметром; без базовой дозы — 422 vra_no_base_dose.

Геометрия зон: бинарные маски пиковой сцены векторизуются (rasterio.features.
shapes), упрощаются в CRS растра (~1 м) и переводятся в WGS84; площади зон —
пиксельные (сходятся с API-52).
"""

import io
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from sqlalchemy.orm import Session

from app.models.agronomy import Field
from app.modules.satellite import vra

#: упрощение контуров зон в CRS растра (метры), #125
ZONE_SIMPLIFY_M = 1.0

#: DDI 1 — норма внесения, кг/га (ISO 11783-11 Data Dictionary)
DDI_RATE_KG_HA = 1


@dataclass(frozen=True, slots=True)
class ExportZone:
    """Зона выгрузки: атрибуты API-52 + полигон WGS84."""

    zone: int
    label: str
    area_ga: float
    ndvi_mean: float
    dose_factor: float
    dose_kg_ha: float
    geometry: object  # shapely MultiPolygon, WGS84


@dataclass(frozen=True, slots=True)
class ExportedVra:
    """Выгрузка целиком: пик-сцена + зоны с геометрией и дозами."""

    captured_at: date
    base_dose_kg_ha: float
    zones: list[ExportZone]


def _vectorize_zones(result: vra.VraZones) -> list[ExportZone] | None:
    """Маски зон → упрощённые полигоны WGS84 (None — зон/масок нет)."""
    if not result.zones or result.zone_masks is None or result.grid_transform is None:
        return None
    transform = Affine(*result.grid_transform)
    crs = result.grid_crs or "EPSG:32637"
    zones: list[ExportZone] = []
    for zone_row, mask in zip(result.zones, result.zone_masks, strict=False):
        polygons = []
        for geom, value in shapes(
            mask.astype("uint8"), mask=mask, transform=transform
        ):
            if int(value) != 1:
                continue
            poly = shape(geom).simplify(ZONE_SIMPLIFY_M)
            if not poly.is_empty and poly.area > 0:
                polygons.append(poly)
        if not polygons:
            continue
        multi = unary_union(polygons)
        # метры растра → WGS84 (long/lat): через GeoJSON-словарь растра
        wgs = shape(transform_geom(crs, "EPSG:4326", mapping(multi)))
        zones.append(
            ExportZone(
                zone=zone_row.zone,
                label=zone_row.label,
                area_ga=zone_row.area_ga,
                ndvi_mean=zone_row.ndvi_mean,
                dose_factor=zone_row.dose_factor,
                dose_kg_ha=round(result.base_dose * zone_row.dose_factor, 2)
                if getattr(result, "base_dose", None)
                else 0.0,
                geometry=wgs,
            )
        )
    return zones or None


def build_export(
    db: Session,
    field: Field,
    *,
    year: int | None = None,
    base_dose: float | None = None,
) -> ExportedVra:
    """Полный расчёт зон выгрузки: пик сезона → маски → полигоны + дозы.

    Дозы: явный base_dose приоритетнее approved-плана (vra.base_dose_kg_ha).
    """
    dose = base_dose if base_dose is not None else vra.base_dose_kg_ha(db, field.id)
    if dose is None or dose <= 0:
        raise vra.AppError(
            f"У поля {field.id} нет базовой дозы для выгрузки: нет approved "
            "fertilizing-плана (kg_ha) и не задан параметр base_dose_kg_ha",
            code="vra_no_base_dose",
            title="Нет базовой дозы",
            status_code=422,
        )

    peaks = vra.peak_scenes(db, field.id, year=year)
    if not peaks:
        if year is not None:
            raise vra.AppError(
                f"У поля {field.id} нет NDVI-сцен сезона {year}",
                code="season_not_found",
                title="Сезон не найден",
                status_code=404,
            )
        raise vra.AppError(
            f"Зоны внесения недоступны для поля {field.id}: "
            "нет чистых NDVI-сцен текущего сезона",
            code="vra_no_season_data",
            title="Нет данных сезона",
            status_code=422,
        )
    # зоны по пиковой сцене с максимальным index_avg (§7.4) — живой расчёт:
    # маски в кэш не пишутся, поэтому без _scene_vra
    scene = max(peaks, key=lambda s: (s.index_avg, s.captured_at))
    result = vra._analyze_scene(db, field, scene)  # noqa: SLF001 — точка расширения экспорта

    vectorized = _vectorize_zones(result)
    if vectorized is None:
        raise vra.AppError(
            f"Не удалось векторизовать зоны поля {field.id}",
            code="vra_export_geometry",
            title="Ошибка выгрузки",
            status_code=422,
        )
    zones = [
        ExportZone(
            zone=z.zone,
            label=z.label,
            area_ga=z.area_ga,
            ndvi_mean=z.ndvi_mean,
            dose_factor=z.dose_factor,
            dose_kg_ha=round(dose * z.dose_factor, 2),
            geometry=z.geometry,
        )
        for z in vectorized
    ]
    return ExportedVra(captured_at=result.captured_at, base_dose_kg_ha=dose, zones=zones)


def geojson_bytes(data: ExportedVra) -> bytes:
    """GeoJSON FeatureCollection: фича на зону с дозой (API-57 geojson)."""
    import json

    features = [
        {
            "type": "Feature",
            "properties": {
                "zone": z.zone,
                "label": z.label,
                "area_ga": z.area_ga,
                "ndvi_mean": z.ndvi_mean,
                "dose_factor": z.dose_factor,
                "dose_kg_ha": z.dose_kg_ha,
            },
            "geometry": mapping(z.geometry),
        }
        for z in data.zones
    ]
    payload = {
        "type": "FeatureCollection",
        "name": f"vra_{data.captured_at.isoformat()}",
        "features": features,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def shapefile_zip(data: ExportedVra, field_name: str) -> bytes:
    """ZIP: SHP+SHX+DBF+PRJ+CPG; полигон на зону (API-57 shp)."""
    import shapefile

    buf = io.BytesIO()
    writer = shapefile.Writer(buf, shapeType=shapefile.POLYGON, encoding="utf-8")
    writer.field("zone", "N", decimal=0)
    writer.field("label", "C", size=40)
    writer.field("area_ga", "N", size=12, decimal=2)
    writer.field("ndvi_mean", "N", size=8, decimal=3)
    writer.field("dose_fact", "N", size=6, decimal=2)
    writer.field("dose_kg_h", "N", size=10, decimal=2)
    for z in data.zones:
        geom = z.geometry  # shapely MultiPolygon/Polygon WGS84
        polys = (
            list(getattr(geom, "geoms", [geom]))
            if hasattr(geom, "geoms")
            else [geom]
        )
        parts = []
        for poly in polys:
            exterior = [
                [round(x, 7), round(y, 7)] for x, y in poly.exterior.coords
            ]
            parts.append({"ring": exterior})
            for interior in poly.interiors:
                parts.append(
                    {"ring": [[round(x, 7), round(y, 7)] for x, y in interior.coords]}
                )
        writer.poly([parts])
        writer.record(
            z.zone, z.label, z.area_ga, z.ndvi_mean, z.dose_factor, z.dose_kg_ha
        )
    writer.close()
    shp_blob = buf.getvalue()

    stem = "vra_zones"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # pyshp пишет одним блобом: шейп-записи едины, dbf-часть — тем же writer
        zf.writestr(f"{stem}.shp", shp_blob)
        # writer держит shx/dbf внутри; перевыпускаем по частям
        zf.writestr(f"{stem}.shx", writer.shxBytes())
        zf.writestr(f"{stem}.dbf", writer.dbfBytes())
        zf.writestr(
            f"{stem}.prj",
            'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
            'SPHEROID["WGS_1984",6378137,298.257223563]],'
            'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]',
        )
        zf.writestr(f"{stem}.cpg", "UTF-8")
        zf.writestr("README.txt", f"Карта дифвнесения {field_name}: {len(data.zones)} зон\n")
    return out.getvalue()


def _pnt(parent: Element, lon: float, lat: float) -> None:
    """PNT: точка контура в миллионных долях градуса (ISO 11783-10)."""
    pnt = SubElement(parent, "PNT", {"A": "2"})
    pnt.set("D", str(int(round(lon * 1_000_000))))
    pnt.set("E", str(int(round(lat * 1_000_000))))


def _polygon_xml(parent: Element, geom: object, polygon_type: str) -> None:
    """PLN>LSG>PNT* — внешний контур каждого полигона зоны (кольца — отдельные LSG)."""
    polys = list(getattr(geom, "geoms", [geom]))
    for poly in polys:
        for ring, lsg_type in ((poly.exterior, "1"), *((i, "2") for i in poly.interiors)):
            pln = SubElement(parent, "PLN", {"A": polygon_type})
            lsg = SubElement(pln, "LSG", {"A": lsg_type})
            for lon, lat in ring.coords:
                _pnt(lsg, lon, lat)


def isoxml_zip(data: ExportedVra, field: Field) -> bytes:
    """ISOXML (ISO 11783-10 TASKDATA v4): partfield + зоны как TreatmentZone.

    Prescription partfield-подход: PLN type 1 — граница поля, зоны — TRT с
    PDV DDI 1 (кг/га) и PLN type 2. Структура собрана вручную по ISO 11783-10;
    проверка на реальных терминалах не проводилась (см. docs/06 §7.4).
    """
    root = Element(
        "ISO11783_TaskData",
        {
            "versionMajor": "4",
            "versionMinor": "0",
            "dataOrigin": "1",
            "taskControllerVersion": "4",
            "dataTransferMode": "1",
        },
    )
    SubElement(root, "FRM", {"A": "CDA"})
    SubElement(root, "PDT", {"A": "Удобрение (дифвнесение)"})

    pfd = SubElement(root, "PFD", {"A": f"{field.name} {data.captured_at.isoformat()}"})
    boundary = unary_union([z.geometry for z in data.zones]).convex_hull
    _polygon_xml(pfd, boundary, "1")
    for z in data.zones:
        trt = SubElement(pfd, "TRT", {"A": f"Зона {z.zone} ({z.label})"})
        pdv = SubElement(trt, "PDV", {"D": str(DDI_RATE_KG_HA)})
        pdv.set("E", str(int(round(z.dose_kg_ha * 100))))  # см. DDI: 0.01 кг/га
        _polygon_xml(trt, z.geometry, "2")

    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode").encode()

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("TASKDATA.XML", xml)
    return out.getvalue()


__all__ = [
    "DDI_RATE_KG_HA",
    "ExportZone",
    "ExportedVra",
    "ZONE_SIMPLIFY_M",
    "build_export",
    "geojson_bytes",
    "isoxml_zip",
    "shapefile_zip",
]
