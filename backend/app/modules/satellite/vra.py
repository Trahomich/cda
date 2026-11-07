"""Зоны дифференцированного внесения (VRA) — docs/06 §7.4 (v3-04, #94).

Расчёт по требованию: последняя чистая (cloud ≤ 60) NDVI-сцена поля, окно
COG b04/b08/scl перечитывается (как в рендерах §7.3: целочисленное окно по
конуру, SCL → сетка 10 м nearest, прореживание до 1024 px), NDVI внутри
конура (без облаков) кластеризуется на 3 зоны по перцентилям
(<30-й, 30–70-й, >70-й). Поле однородно (IQR NDVI < 0.12) — 422
vra_uniform_field, зоны не строятся.

Кэш 24 ч по (field_id, дата сцены) в Redis/Dragonfly (soft-fail, §7.4);
PNG зон (3 оттенка красный→жёлтый→зелёный) кэшируется вместе с зонами.

Дозы — рекомендация: dose_factor 1.15/1.0/0.85 (пониженное развитие —
дозу выше), база — последний approved план fertilizing (units kg_ha).
"""

import base64
import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
from sqlalchemy import select, func
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


def _err_no_data(field_id: uuid.UUID, reason: str) -> AppError:
    return AppError(
        f"Зоны внесения недоступны для поля {field_id}: {reason}",
        code="vra_no_data",
        title="Нет данных",
        status_code=422,
    )


def latest_scene(db: Session, field_id: uuid.UUID) -> SatelliteScene | None:
    """Последняя чистая NDVI-сцена поля (в БД только cloud ≤ 60, 05 §5)."""
    return db.scalars(
        select(SatelliteScene)
        .where(SatelliteScene.field_id == field_id, SatelliteScene.index == "ndvi")
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
    """Расчёт зон по COG последней чистой сцены (§7.4): окно как в §7.3."""
    from rasterio.transform import Affine
    from rasterio.warp import transform_bounds, transform_geom

    assets = _scene_assets(db, field.id, scene.captured_at)
    if not assets:
        raise _err_no_data(field.id, f"нет ассетов каталога за {scene.captured_at.isoformat()}")

    contour_geojson = json.loads(
        db.scalar(select(func.ST_AsGeoJSON(Field.contour)).where(Field.id == field.id))
    )
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
        raise AppError(
            f"Поле однородно: IQR NDVI = {iqr:.3f} < {VRA_MIN_IQR} — зоны внесения не строятся",
            code="vra_uniform_field",
            title="Поле однородно",
            status_code=422,
        )

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
        )
    except (KeyError, ValueError, TypeError):
        logger.warning("VRA: битый кэш — пересчитаю", exc_info=True)
        return None


def vra_zones(db: Session, field: Field) -> VraZones:
    """Зоны поля с кэшем 24 ч по (field_id, дата сцены) — §7.4."""
    scene = latest_scene(db, field.id)
    if scene is None:
        raise _err_no_data(field.id, "нет чистых NDVI-сцен")
    key = f"vra:{field.id}:{scene.captured_at.isoformat()}"
    if (cached := cache_get(key)) is not None and (parsed := _from_cache(cached)) is not None:
        return parsed
    result = compute_vra(db, field, scene)
    cache_set(key, _to_cache(result), CACHE_TTL_SECONDS)
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
    "vra_zones",
]
