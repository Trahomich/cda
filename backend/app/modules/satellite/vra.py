"""Зоны дифференцированного внесения (VRA) — docs/06 §7.4 (v3-04 #94, v1.7 #121).

Расчёт по требованию: ПИКОВЫЕ чистые (cloud ≤ 60) NDVI-сцены сезона —
топ-3 по index_avg (последние снимки осенью однородны: вегетации нет).
v1.7 (фидбек #364): ворота однородности УДАЛЕНЫ — зоны строятся ВСЕГДА,
когда у поля есть чистые сцены сезона («просто записать результаты в
дифвнесения»); 422 vra_uniform_field больше не возвращается. Зоны
(кластеризация на 3 по перцентилям <30-й, 30–70-й, >70-й) и PNG строятся
по одной сцене — с максимальным index_avg.

Однородность — не блокировка, а информационные метрики (§7.4 v1.7):
``iqr_ndvi`` — пиксельный IQR пиковой сцены, ``spatial_spread`` —
p90−p10 средних ячеек сетки ~20×20 той же сцены (ячейка ≥ 30 валидных
пикселей): мелкие пятна ~1 га глобальный IQR не видит (измерено на поле
50: пятна 0.66–0.74 при фоне 0.78–0.80, пиксельный IQR 0.03).

Кэш 24 ч в Redis/Dragonfly (soft-fail, §7.4): полный расчёт — по
(field_id, дата сцены); указатель текущих зон с метриками всех пиковых
сцен — отдельным ключом (детектор §8.2, #121); PNG зон кэшируется вместе
с зонами.

Дозы — рекомендация: dose_factor 1.15/1.0/0.85 (пониженное развитие —
дозу выше), база — последний approved план fertilizing (units kg_ha).
"""

import base64
import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import cache_get, cache_set
from app.core.errors import AppError
from app.integrations import DataGapError, SourceUnavailableError
from app.integrations.raster import compute_index_array, contour_mask
from app.models.agronomy import AgroInput, CropCycle, Field, TreatmentPlan
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.ndvi import (
    CLOUD_SCL_VALUES,
    _read_band_window,
    _read_scl_grid,
)
from app.services.sentinel_ingest.renders import MAX_SIDE_PX, draw_zone_legend

logger = logging.getLogger(__name__)

#: пиксельный порог неоднородности (§7.4): IQR NDVI ≥ 0.12 — детектор §8.2
VRA_MIN_IQR = 0.12

#: пространственный порог неоднородности (§7.4 v1.7): p90−p10 средних
#: ячеек сетки ≥ 0.06 — мелкие пятна ~1 га (детектор §8.2, #121)
VRA_SPREAD_MIN = 0.06

#: сетка пространственного разброса: ~20×20 ячеек, ячейка ≥ 30 валидных px
SPREAD_GRID = 20
SPREAD_CELL_MIN_PX = 30

#: кэш расчёта по (field_id, дата сцены) — 24 ч (§7.4)
CACHE_TTL_SECONDS = 24 * 3600

#: зоны: подписи, множители дозы и цвета PNG (красный → жёлтый → зелёный)
ZONE_LABELS = ("пониженное развитие", "среднее развитие", "повышенное развитие")
ZONE_DOSE_FACTORS = (1.15, 1.0, 0.85)
ZONE_COLORS = ((192, 57, 43), (241, 196, 15), (39, 174, 96))


@dataclass(frozen=True, slots=True)
class VraZone:
    """Одна зона: номер, подпись, площадь, средний NDVI, множитель дозы."""

    zone: int
    label: str
    area_ga: float
    ndvi_mean: float
    dose_factor: float


@dataclass(frozen=True, slots=True)
class VraZones:
    """Расчёт зон поля: сцена, метрики, зоны, PNG и геопривязка (§7.4)."""

    captured_at: date
    iqr_ndvi: float
    zones: list[VraZone]
    png: bytes
    width: int
    height: int
    bbox: tuple[float, float, float, float]  # WGS84: minLon, minLat, maxLon, maxLat
    #: даты топ-3 пиковых сцен сезона (метрики для детектора §8.2, #121)
    peak_dates: tuple[date, ...] = ()
    #: p90−p10 средних ячеек сетки ~20×20 пиковой сцены (§7.4 v1.7)
    spatial_spread: float = 0.0


def _err_no_data(field_id: uuid.UUID, reason: str) -> AppError:
    return AppError(
        f"Зоны внесения недоступны для поля {field_id}: {reason}",
        code="vra_no_data",
        title="Нет данных",
        status_code=422,
    )


def latest_scene(
    db: Session, field_id: uuid.UUID, *, year: int | None = None
) -> SatelliteScene | None:
    """Последняя чистая NDVI-сцена поля (в БД только cloud ≤ 60, 05 §5).

    year — сцены только сезона YYYY (§7.5).
    """
    filters = [SatelliteScene.field_id == field_id, SatelliteScene.index == "ndvi"]
    if year is not None:
        filters.append(func.extract("year", SatelliteScene.captured_at) == year)
    return db.scalars(
        select(SatelliteScene)
        .where(*filters)
        .order_by(SatelliteScene.captured_at.desc(), SatelliteScene.created_at.desc())
        .limit(1)
    ).first()


def _scene_assets(db: Session, field_id: uuid.UUID, captured_at: date) -> dict:
    """Ассеты COG каталога для даты сцены (первая строка с каналами)."""
    items = db.scalars(
        select(SatelliteCatalogItem)
        .where(
            SatelliteCatalogItem.field_id == field_id,
            SatelliteCatalogItem.captured_at == captured_at,
        )
        .order_by(SatelliteCatalogItem.created_at)
    ).all()
    for item in items:
        if all(item.assets.get(band) for band in ("b04", "b08", "scl")):
            return item.assets
    return {}


def compute_vra(db: Session, field: Field, scene: SatelliteScene) -> VraZones:
    """Расчёт зон по COG сцены (§7.4 v1.7): окно как в §7.3, зоны — всегда."""
    return _analyze_scene(db, field, scene)


def _spatial_spread(ndvi: np.ndarray, valid: np.ndarray) -> float:
    """p90−p10 средних ячеек сетки ~SPREAD_GRID×SPREAD_GRID (§7.4 v1.7).

    Ячейка учитывается при ≥ SPREAD_CELL_MIN_PX валидных пикселей; меньше
    двух валидных ячеек — 0.0 (пространственной информации нет). Мелкие
    пятна ~1 га глобальный IQR не видит, средние ячеек — видят.
    """
    h, w = ndvi.shape
    ny, nx = min(SPREAD_GRID, h), min(SPREAD_GRID, w)
    if ny < 1 or nx < 1:
        return 0.0
    y_bounds = np.linspace(0, h, ny + 1).astype(int)
    x_bounds = np.linspace(0, w, nx + 1).astype(int)
    means: list[float] = []
    for i in range(ny):
        for j in range(nx):
            cell = (slice(y_bounds[i], y_bounds[i + 1]), slice(x_bounds[j], x_bounds[j + 1]))
            cell_valid = valid[cell]
            if int(cell_valid.sum()) < SPREAD_CELL_MIN_PX:
                continue
            means.append(float(ndvi[cell][cell_valid].mean()))
    if len(means) < 2:
        return 0.0
    return round(float(np.percentile(means, 90) - np.percentile(means, 10)), 3)


def _analyze_scene(db: Session, field: Field, scene: SatelliteScene) -> VraZones:
    """Зоны по COG сцены (§7.4 v1.7): всегда — кластеризация на 3 по
    перцентилям, метрики iqr_ndvi и spatial_spread той же сцены.

    Общий путь чтения COG (окно как в §7.3: целочисленное окно по контуру,
    SCL → сетка 10 м nearest, прореживание до 1024 px).
    """
    from rasterio.transform import Affine
    from rasterio.warp import transform_bounds, transform_geom

    assets = _scene_assets(db, field.id, scene.captured_at)
    if not assets:
        raise _err_no_data(field.id, f"нет ассетов каталога за {scene.captured_at.isoformat()}")

    geojson_str = db.scalar(select(func.ST_AsGeoJSON(Field.contour)).where(Field.id == field.id))
    if geojson_str is None:
        raise _err_no_data(field.id, "контур поля недоступен")
    contour_geojson = json.loads(geojson_str)
    try:
        red_arr, red_transform, red_crs = _read_band_window(assets["b04"], contour_geojson)
        nir_arr, nir_transform, _ = _read_band_window(assets["b08"], contour_geojson)
        if red_arr.shape != nir_arr.shape or red_transform != nir_transform:
            raise DataGapError("каналы b04/b08 на разных сетках — окно не совпадает")
        scl = _read_scl_grid(assets, contour_geojson, nir_transform, red_crs, nir_arr.shape)
    except SourceUnavailableError as exc:
        raise AppError(
            f"Спутниковые данные недоступны: {exc}",
            code="vra_source_unavailable",
            title="Источник недоступен",
            status_code=503,
        ) from exc
    except DataGapError as exc:
        raise _err_no_data(field.id, str(exc)) from exc

    # прореживание до MAX_SIDE_PX (ближайший сосед) — сетка общая для зон и PNG
    factor = max(1, math.ceil(max(red_arr.shape) / MAX_SIDE_PX))
    sl = (slice(None, None, factor), slice(None, None, factor))
    transform = red_transform * Affine.scale(factor, factor)
    nir_d = nir_arr[sl].astype("float32")
    red_d = red_arr[sl].astype("float32")
    cloud_d = np.isin(scl[sl], CLOUD_SCL_VALUES)
    inside = contour_mask(
        transform_geom("EPSG:4326", red_crs, contour_geojson), transform, nir_d.shape
    )

    ndvi = compute_index_array("ndvi", nir_d, red_d)
    valid = inside & np.isfinite(ndvi) & ~cloud_d
    if not valid.any():
        raise _err_no_data(field.id, "внутри контура нет чистых пикселей с данными")
    values = ndvi[valid]
    p25, p30, p70, p75 = np.percentile(values, (25.0, 30.0, 70.0, 75.0))
    iqr = round(float(p75 - p25), 3)
    spread = _spatial_spread(ndvi, valid)

    zone_masks = (
        valid & (ndvi < p30),  # 1 — пониженное развитие
        valid & (ndvi >= p30) & (ndvi <= p70),  # 2 — среднее
        valid & (ndvi > p70),  # 3 — повышенное
    )
    field_area = float(field.area_ga or 0.0)
    total_pixels = int(valid.sum())
    zones = [
        VraZone(
            zone=number,
            label=ZONE_LABELS[number - 1],
            area_ga=round(field_area * int(mask.sum()) / total_pixels, 2),
            ndvi_mean=round(float(ndvi[mask].mean()), 3),
            dose_factor=ZONE_DOSE_FACTORS[number - 1],
        )
        for number, mask in enumerate(zone_masks, start=1)
        if mask.any()
    ]

    # PNG зон: заливка 3 оттенками, вне контура/облака — прозрачные (§7.4)
    from app.services.sentinel_ingest.renders import _png_rgba

    rgb = np.zeros(nir_d.shape + (3,), dtype="uint8")
    alpha = np.zeros(nir_d.shape, dtype="uint8")
    for mask, color in zip(zone_masks, ZONE_COLORS, strict=False):
        rgb[mask] = color
        alpha[mask] = 255
    png = _png_rgba(rgb, alpha)

    left, top = transform * (0, 0)
    right, bottom = transform * (nir_d.shape[1], nir_d.shape[0])
    west, south, east, north = transform_bounds(
        str(red_crs), "EPSG:4326", left, bottom, right, top, densify_pts=21
    )
    bbox = tuple(round(v, 7) for v in (west, south, east, north))

    return VraZones(
        captured_at=scene.captured_at,
        iqr_ndvi=iqr,
        zones=zones,
        png=png,
        width=nir_d.shape[1],
        height=nir_d.shape[0],
        bbox=bbox,  # type: ignore[arg-type]
        spatial_spread=spread,
    )


def _to_cache(vra: VraZones) -> dict:
    return {
        "captured_at": vra.captured_at.isoformat(),
        "iqr_ndvi": vra.iqr_ndvi,
        "spatial_spread": vra.spatial_spread,
        "zones": [asdict(z) for z in vra.zones],
        "png": base64.b64encode(vra.png).decode("ascii"),
        "width": vra.width,
        "height": vra.height,
        "bbox": list(vra.bbox),
        "peak_dates": [d.isoformat() for d in vra.peak_dates],
    }


def _from_cache(data: dict) -> VraZones | None:
    try:
        return VraZones(
            captured_at=date.fromisoformat(data["captured_at"]),
            iqr_ndvi=float(data["iqr_ndvi"]),
            zones=[
                VraZone(
                    zone=int(z["zone"]),
                    label=z["label"],
                    area_ga=float(z["area_ga"]),
                    ndvi_mean=float(z["ndvi_mean"]),
                    dose_factor=float(z["dose_factor"]),
                )
                for z in data["zones"]
            ],
            png=base64.b64decode(data["png"]),
            width=int(data["width"]),
            height=int(data["height"]),
            bbox=tuple(float(v) for v in data["bbox"]),  # type: ignore[arg-type]
            peak_dates=tuple(date.fromisoformat(d) for d in data.get("peak_dates", [])),
            # до v1.7 (#121) метрики spread в кэше не было — KeyError → пересчёт
            spatial_spread=float(data["spatial_spread"]),
        )
    except (KeyError, ValueError, TypeError):
        logger.warning("VRA: битый/старый кэш — пересчитаю", exc_info=True)
        return None


def peak_scenes(
    db: Session, field_id: uuid.UUID, *, year: int | None = None
) -> list[SatelliteScene]:
    """Топ-3 чистых NDVI-сцены сезона по index_avg (§7.4 v1.4), по возрастанию даты.

    Сезон по умолчанию (year=None) — календарный год последней сцены поля
    (задача #106: зоны вегетации видны на пиковых снимках, а не на последних,
    где вегетации уже нет). Явный year — сцены сезона YYYY (§7.5).
    """
    latest = latest_scene(db, field_id, year=year)
    if latest is None:
        return []
    rows = db.scalars(
        select(SatelliteScene)
        .where(
            SatelliteScene.field_id == field_id,
            SatelliteScene.index == "ndvi",
            func.extract("year", SatelliteScene.captured_at) == latest.captured_at.year,
        )
        .order_by(SatelliteScene.index_avg.desc(), SatelliteScene.captured_at.desc())
        .limit(3)
    ).all()
    return sorted(rows, key=lambda s: s.captured_at)


def _scene_vra(db: Session, field: Field, scene: SatelliteScene) -> VraZones:
    """Полный расчёт по сцене с кэшем 24 ч (§7.4 v1.7): зоны строятся всегда,
    ключ vra:{field_id}:{date} (записи до #121 без spatial_spread
    пересчитываются — _from_cache вернёт None).
    """
    key = f"vra:{field.id}:{scene.captured_at.isoformat()}"
    if (cached := cache_get(key)) is not None and (parsed := _from_cache(cached)) is not None:
        return parsed
    zones = _analyze_scene(db, field, scene)
    cache_set(key, _to_cache(zones), CACHE_TTL_SECONDS)
    return zones


def vra_zones(db: Session, field: Field, *, year: int | None = None) -> VraZones:
    """Зоны по пиковым сценам вегетации (§7.4 v1.7): топ-3 по index_avg,
    зоны и метрики — по сцене с максимальным index_avg (ворота однородности
    удалены, #121: «просто записать результаты в дифвнесения»).

    Явный year (§7.5) — сцены сезона YYYY; сезона нет — 404 season_not_found.
    Без year — текущий сезон (год последней сцены)."""
    peaks = peak_scenes(db, field.id, year=year)
    if not peaks:
        if year is not None:
            raise AppError(
                f"У поля {field.id} нет NDVI-сцен сезона {year}",
                code="season_not_found",
                title="Сезон не найден",
                status_code=404,
            )
        raise AppError(
            f"Зоны внесения недоступны для поля {field.id}: нет чистых NDVI-сцен текущего сезона",
            code="vra_no_season_data",
            title="Нет данных сезона",
            status_code=422,
        )
    peak_dates = tuple(scene.captured_at for scene in peaks)
    analyzed = [(scene, _scene_vra(db, field, scene)) for scene in peaks]

    # зоны строим по пиковой сцене с максимальным index_avg (§7.4)
    scene, result = max(analyzed, key=lambda row: (row[0].index_avg, row[0].captured_at))
    result = replace(result, peak_dates=peak_dates)
    # указатель для «Выводов ассистента»: актуальные зоны + метрики всех
    # пиковых сцен (детектор §8.2 v1.7, #121); сезонный год (§7.5) —
    # отдельный ключ, ключ текущего сезона не трогаем
    pointer_key = f"vra:peak:{field.id}" if year is None else f"vra:peak:{field.id}:{year}"
    cache_set(
        pointer_key,
        {
            "date": result.captured_at.isoformat(),
            "iqr_ndvi": result.iqr_ndvi,
            "spatial_spread": result.spatial_spread,
            "peaks": [
                {
                    "date": row.captured_at.isoformat(),
                    "iqr_ndvi": zones.iqr_ndvi,
                    "spatial_spread": zones.spatial_spread,
                }
                for row, zones in analyzed
            ],
        },
        CACHE_TTL_SECONDS,
    )
    return result


def base_dose_kg_ha(db: Session, field_id: uuid.UUID) -> float | None:
    """Доза последнего approved fertilizing-плана поля, кг/га (иначе null).

    Берутся только планы с единицей препарата kg_ha (l_ha и кг д.в.
    несводимы к кг/га продукта без пересчёта).
    """
    dose = db.execute(
        select(TreatmentPlan.dose)
        .join(CropCycle, TreatmentPlan.cycle_id == CropCycle.id)
        .join(AgroInput, TreatmentPlan.input_id == AgroInput.id)
        .where(
            CropCycle.field_id == field_id,
            TreatmentPlan.operation_type == "fertilizing",
            TreatmentPlan.status == "approved",
            AgroInput.unit == "kg_ha",
        )
        .order_by(TreatmentPlan.planned_date.desc().nulls_last(), TreatmentPlan.created_at.desc())
        .limit(1)
    ).scalar()
    return float(dose) if dose is not None else None


__all__ = [
    "CACHE_TTL_SECONDS",
    "VRA_MIN_IQR",
    "VraZone",
    "VraZones",
    "base_dose_kg_ha",
    "compute_vra",
    "peak_scenes",
    "vra_zones",
]
