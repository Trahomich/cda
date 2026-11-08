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
from app.services.sentinel_ingest.renders import MAX_SIDE_PX

logger = logging.getLogger(__name__)

#: минимальная неоднородность для построения зон (§7.4): IQR NDVI ≥ 0.12
VRA_MIN_IQR = 0.12

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
    """Расчёт зон поля: сцена, зоны, PNG и геопривязка (§7.4)."""

    captured_at: date
    iqr_ndvi: float
    zones: list[VraZone]
    png: bytes
    width: int
    height: int
    bbox: tuple[float, float, float, float]  # WGS84: minLon, minLat, maxLon, maxLat
    #: даты топ-3 пиковых сцен сезона, по которым подтверждалась стабильность
    peak_dates: tuple[date, ...] = ()


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
    """Расчёт зон по COG сцены (§7.4): окно как в §7.3; однородность — 422."""
    iqr, zones = _analyze_scene(db, field, scene)
    if zones is None:
        raise _uniform_error(iqr, (scene.captured_at,))
    return zones


def _uniform_error(iqr: float, peak_dates: tuple[date, ...] | list[date]) -> AppError:
    """422 vra_uniform_field: IQR ниже порога на пиковых сценах (§7.4 v1.4)."""
    dates_str = ", ".join(d.isoformat() for d in peak_dates)
    return AppError(
        f"Поле однородно по пиковым сценам ({dates_str}): IQR NDVI < {VRA_MIN_IQR} "
        "— зоны внесения не строятся",
        code="vra_uniform_field",
        title="Поле однородно",
        status_code=422,
    )


def _analyze_scene(
    db: Session, field: Field, scene: SatelliteScene
) -> tuple[float, VraZones | None]:
    """IQR NDVI сцены и (если поле неоднородно) полные зоны; None — однородно.

    Общий путь чтения COG для пиковых сцен: IQR нужен каждой сцене для
    подтверждения стабильности (§7.4 v1.4), зоны — только выбранной.
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
    if iqr < VRA_MIN_IQR:
        return iqr, None  # однородная сцена: зон нет, IQR нужен для стабильности

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

    return iqr, VraZones(
        captured_at=scene.captured_at,
        iqr_ndvi=iqr,
        zones=zones,
        png=png,
        width=nir_d.shape[1],
        height=nir_d.shape[0],
        bbox=bbox,  # type: ignore[arg-type]
    )


def _to_cache(vra: VraZones) -> dict:
    return {
        "captured_at": vra.captured_at.isoformat(),
        "iqr_ndvi": vra.iqr_ndvi,
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
        )
    except (KeyError, ValueError, TypeError):
        logger.warning("VRA: битый кэш — пересчитаю", exc_info=True)
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


def _scene_iqr_and_zones(
    db: Session, field: Field, scene: SatelliteScene
) -> tuple[float, VraZones | None]:
    """(IQR, зоны|null) пиковой сцены с кэшем 24 ч по дате сцены (§7.4).

    Полный расчёт кэшируется под vra:{field_id}:{date}; однородная сцена —
    только IQR под vra:iqr:{field_id}:{date} (зон у неё нет, перечитывать
    COG ради IQR не нужно).
    """
    key = f"vra:{field.id}:{scene.captured_at.isoformat()}"
    if (cached := cache_get(key)) is not None and (parsed := _from_cache(cached)) is not None:
        return parsed.iqr_ndvi, parsed
    iqr_key = f"vra:iqr:{field.id}:{scene.captured_at.isoformat()}"
    iqr_cached = cache_get(iqr_key)
    if isinstance(iqr_cached, dict) and isinstance(iqr_cached.get("iqr_ndvi"), (int, float)):
        return float(iqr_cached["iqr_ndvi"]), None
    iqr, zones = _analyze_scene(db, field, scene)
    if zones is None:
        cache_set(iqr_key, {"iqr_ndvi": iqr}, CACHE_TTL_SECONDS)
    else:
        cache_set(key, _to_cache(zones), CACHE_TTL_SECONDS)
    return iqr, zones


def vra_zones(db: Session, field: Field, *, year: int | None = None) -> VraZones:
    """Зоны по пиковым сценам вегетации (§7.4 v1.4): топ-3 по index_avg,
    стабильность — IQR ≥ порога минимум на 2 из 3 (при <3 сцен — на всех,
    кроме максимум одной); зоны — по сцене с максимальным index_avg.

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
        raise _err_no_data(field.id, "нет чистых NDVI-сцен")
    peak_dates = tuple(scene.captured_at for scene in peaks)
    analyzed = [(scene, *_scene_iqr_and_zones(db, field, scene)) for scene in peaks]
    confirmed = [row for row in analyzed if row[1] >= VRA_MIN_IQR]
    required = max(1, len(peaks) - 1)  # 3 сцены → 2; 2 → 1; 1 → сама сцена
    if len(confirmed) < required:
        raise _uniform_error(min(row[1] for row in analyzed), peak_dates)

    # зоны строим по подтверждённой сцене с максимальным index_avg (§7.4)
    scene, _, zones = max(confirmed, key=lambda row: (row[0].index_avg, row[0].captured_at))
    assert zones is not None  # подтверждённая сцена неоднородна по построению
    result = replace(zones, peak_dates=peak_dates)
    # указатель для «Выводов ассистента»: актуальные зоны поля (§8.2);
    # сезонный год (§7.5) — отдельный ключ, ключ текущего сезона не трогаем
    pointer_key = f"vra:peak:{field.id}" if year is None else f"vra:peak:{field.id}:{year}"
    cache_set(
        pointer_key,
        {"date": result.captured_at.isoformat(), "iqr_ndvi": result.iqr_ndvi},
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
