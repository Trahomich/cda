"""Экспорт карты зон дифференцированного внесения (API-57, docs/06 §7.4, #125).

Три формата: ISOXML (ISO 11783-10 TASKDATA, бортовые терминалы), Shapefile
(ГИС/агроконсультанты) и GeoJSON. Дозы — из последнего approved fertilizing-
плана (kg_ha) или явным параметром; без базовой дозы — 422 vra_no_base_dose.

Геометрия зон: бинарные маски пиковой сцены векторизуются (rasterio.features.
shapes), упрощаются в CRS растра (~1 м) и переводятся в WGS84; площади зон —
пиксельные (сходятся с API-52).
"""

import io
import zipfile
from dataclasses import dataclass
from datetime import date
from xml.etree.ElementTree import Element, SubElement, tostring

from rasterio.features import shapes
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.agronomy import Field
from app.modules.satellite import vra

#: упрощение контуров зон в CRS растра (метры), #125
ZONE_SIMPLIFY_M = 1.0

#: DDI 1 — норма внесения, кг/га (ISO 11783-11 DD), шаг 0.01 кг/га
DDI_RATE_KG_HA = 1

#: WGS84 в формате ESRI .prj (без pyproj)
PRJ_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'
)


@dataclass(frozen=True, slots=True)
class ExportZone:
    """Зона выгрузки: атрибуты API-52 + полигон WGS84."""

    zone: int
    label: str
    area_ga: float
    ndvi_mean: float
    dose_factor: float
    dose_kg_ha: float
    geometry: BaseGeometry  # MultiPolygon, WGS84


@dataclass(frozen=True, slots=True)
class ExportedVra:
    """Выгрузка целиком: пик-сцена + зоны с геометрией и дозами."""

    captured_at: date
    base_dose_kg_ha: float
    zones: list[ExportZone]


def _zone_geometry(mask, transform: Affine, crs: str) -> BaseGeometry | None:
    """Маска зоны → упрощённый MultiPolygon WGS84 (None — пусто)."""
    polygons = []
    for geom, value in shapes(mask.astype("uint8"), mask=mask, transform=transform):
        if int(value) != 1:
            continue
        poly = shape(geom).simplify(ZONE_SIMPLIFY_M)
        if not poly.is_empty and poly.area > 0:
            polygons.append(poly)
    if not polygons:
        return None
    multi = unary_union(polygons)
    # CRS растра (метры) → WGS84 через GeoJSON-словарь растра
    wgs = shape(transform_geom(crs, "EPSG:4326", mapping(multi)))
    if isinstance(wgs, MultiPolygon):
        return wgs
    return MultiPolygon([wgs]) if wgs.geom_type == "Polygon" else wgs


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
        raise AppError(
            f"У поля {field.id} нет базовой дозы для выгрузки: нет approved "
            "fertilizing-плана (kg_ha) и не задан параметр base_dose_kg_ha",
            code="vra_no_base_dose",
            title="Нет базовой дозы",
            status_code=422,
        )

    peaks = vra.peak_scenes(db, field.id, year=year)
    if not peaks:
        if year is not None:
            raise AppError(
                f"У поля {field.id} нет NDVI-сцен сезона {year}",
                code="season_not_found",
                title="Сезон не найден",
                status_code=404,
            )
        raise AppError(
            f"Зоны внесения недоступны для поля {field.id}: "
            "нет чистых NDVI-сцен текущего сезона",
            code="vra_no_season_data",
            title="Нет данных сезона",
            status_code=422,
        )
    # зоны по пиковой сцене с максимальным index_avg (§7.4) — живой расчёт:
    # маски/привязка в кэш не пишутся, поэтому без _scene_vra
    scene = max(peaks, key=lambda s: (s.index_avg, s.captured_at))
    result = vra._analyze_scene(db, field, scene)  # noqa: SLF001 — точка входа экспорта
    if not result.zones or result.zone_masks is None or result.grid_transform is None:
        raise AppError(
            f"Не удалось векторизовать зоны поля {field.id}",
            code="vra_export_geometry",
            title="Ошибка выгрузки",
            status_code=422,
        )
    transform = Affine(*result.grid_transform)
    crs = result.grid_crs or "EPSG:32637"

    zones: list[ExportZone] = []
    for zone_row, mask in zip(result.zones, result.zone_masks, strict=False):
        geometry = _zone_geometry(mask, transform, crs)
        if geometry is None:
            continue
        zones.append(
            ExportZone(
                zone=zone_row.zone,
                label=zone_row.label,
                area_ga=zone_row.area_ga,
                ndvi_mean=zone_row.ndvi_mean,
                dose_factor=zone_row.dose_factor,
                dose_kg_ha=round(dose * zone_row.dose_factor, 2),
                geometry=geometry,
            )
        )
    if not zones:
        raise AppError(
            f"Не удалось векторизовать зоны поля {field.id}",
            code="vra_export_geometry",
            title="Ошибка выгрузки",
            status_code=422,
        )
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
    """ZIP: SHP+SHX+DBF+PRJ+CPG; одна запись на зону (API-57 shp).

    Части одного shape — внешние и внутренние кольца всех полигонов зоны
    (SHP-полигон трактует кольца по even-odd правилу).
    """
    import shapefile

    w = shapefile.Writer(
        shp=io.BytesIO(),
        shx=io.BytesIO(),
        dbf=io.BytesIO(),
        shapeType=shapefile.POLYGON,
        encoding="utf-8",
    )
    w.field("zone", "N", decimal=0)
    w.field("label", "C", size=40)
    w.field("area_ga", "N", size=12, decimal=2)
    w.field("ndvi_mean", "N", size=8, decimal=3)
    w.field("dose_fact", "N", size=6, decimal=2)
    w.field("dose_kg_ha", "N", size=10, decimal=2)
    for z in data.zones:
        rings: list[list[list[float]]] = []
        for poly in z.geometry.geoms:
            rings.append([[round(x, 7), round(y, 7)] for x, y in poly.exterior.coords])
            rings.extend(
                [[round(x, 7), round(y, 7)] for x, y in interior.coords]
                for interior in poly.interiors
            )
        w.poly(rings)
        w.record(z.zone, z.label, z.area_ga, z.ndvi_mean, z.dose_factor, z.dose_kg_ha)
    w.close()

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("vra_zones.shp", w.shp.getvalue())
        zf.writestr("vra_zones.shx", w.shx.getvalue())
        zf.writestr("vra_zones.dbf", w.dbf.getvalue())
        zf.writestr("vra_zones.prj", PRJ_WGS84)
        zf.writestr("vra_zones.cpg", "UTF-8")
        zf.writestr(
            "README.txt",
            f"Карта дифференцированного внесения, поле «{field_name}», "
            f"пиковая сцена {data.captured_at.isoformat()}, "
            f"базовая доза {data.base_dose_kg_ha:g} кг/га, {len(data.zones)} зон\n",
        )
    return out.getvalue()


def _pnt(lsg: Element, lon: float, lat: float) -> None:
    """PNT type 2 (vertex): координаты в миллионных долях градуса (ISO 11783-10)."""
    SubElement(
        lsg,
        "PNT",
        {"A": "2", "D": str(int(round(lon * 1_000_000))), "E": str(int(round(lat * 1_000_000)))},
    )


def _as_multi(geometry: BaseGeometry) -> list[BaseGeometry]:
    """Polygon → [Polygon], MultiPolygon → список полигонов (для итерации)."""
    if hasattr(geometry, "geoms"):
        return list(geometry.geoms)
    return [geometry]


def _polygon_xml(parent: Element, geometry: BaseGeometry, polygon_type: str) -> None:
    """PLN(type)>LSG>PNT*: внешний контур каждого полигона (кольца — свои LSG)."""
    for poly in _as_multi(geometry):
        rings = [("1", poly.exterior), *(("2", i) for i in poly.interiors)]
        for lsg_type, ring in rings:
            pln = SubElement(parent, "PLN", {"A": polygon_type})
            lsg = SubElement(pln, "LSG", {"A": lsg_type})
            for lon, lat in ring.coords:
                _pnt(lsg, lon, lat)


def isoxml_zip(data: ExportedVra, field: Field) -> bytes:
    """ISOXML (ISO 11783-10 TASKDATA v4): partfield + зоны как TreatmentZone.

    Prescription partfield-подход: PLN type 1 — граница поля, зоны — TRT с
    ProcessData PDV DDI 1 (норма кг/га, шаг 0.01) и PLN type 2. Структура
    собрана вручную по ISO 11783-10; на реальных терминалах не проверялась
    (см. docs/06 §7.4): при первом опыте с техникой сверить с задачей #125.
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
    SubElement(root, "FRM", {"A": "Хозяйство CDA"})
    SubElement(root, "PDT", {"A": "Удобрение (дифвнесение)"})

    pfd = SubElement(root, "PFD", {"A": f"{field.name} {data.captured_at.isoformat()}"})
    # граница поля = объединение зон (кластеризация делит всё валидное поле)
    boundary = unary_union([z.geometry for z in data.zones])
    _polygon_xml(pfd, boundary, "1")
    for z in data.zones:
        trt = SubElement(pfd, "TRT", {"A": f"Зона {z.zone} ({z.label})"})
        SubElement(trt, "PDV", {"D": str(DDI_RATE_KG_HA), "E": str(int(round(z.dose_kg_ha * 100)))})
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
    "PRJ_WGS84",
    "ZONE_SIMPLIFY_M",
    "build_export",
    "geojson_bytes",
    "isoxml_zip",
    "shapefile_zip",
]
