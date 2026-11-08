"""Расчёт индексов §7.7 (docs/06-data-api.md) по COG-активам сцены (#128).

По каждой чистой сцене (cloud ≤ 60) вместе с NDVI рассчитываются и пишутся
отдельными сценами dat_satellite_scene индексы ndwi/ndre/ndmi/ndsi/evi/
savi/bsi (формулы — app/integrations/raster.py). Полосы 20 м (B05, B11)
перепроецируются в сетку B08 (10 м, bilinear) — как SCL в ndvi.py, но для
непрерывных величин. Отсутствующая полоса в активах (строки каталога до
#128) — не ошибка: индексы, которым она нужна, пропускаются.

Пиксели и правила облачности — те же, что у NDVI (контур, SCL ∈ {8,9,10});
статистика — по тем же полезным пикселям. Идемпотентность записи —
UQ(field_id, index, captured_at, source_id), on_conflict_do_nothing.
"""

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date

import numpy as np
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations.raster import (
    compute_bsi_array,
    compute_evi_array,
    compute_index_array,
    contour_mask,
    stats_over_mask,
    validate_index_values,
)
from app.integrations.stac import SATELLITE_SOURCE_ID
from app.models.integrations import SatelliteScene
from app.services.sentinel_ingest.ndvi import (
    CLOUD_SCL_VALUES,
    _open_cog,
    _read_band_window,
    _read_scl_grid,
)

logger = logging.getLogger(__name__)

#: индексы, пишущиеся отдельными сценами помимо ndvi (§7.7 #128)
EXTRA_INDICES: tuple[str, ...] = ("ndwi", "ndre", "ndmi", "ndsi", "evi", "savi", "bsi")

#: полосы (ключи активов каталога), необходимые каждому индексу
INDEX_BANDS_ASSETS: dict[str, tuple[str, ...]] = {
    "ndwi": ("b03", "b08"),
    "ndre": ("b05", "b08"),
    "ndmi": ("b11", "b08"),
    "ndsi": ("b03", "b11"),
    "evi": ("b02", "b04", "b08"),
    "savi": ("b04", "b08"),
    "bsi": ("b02", "b04", "b08", "b11"),
}

#: все полосы расчёта индексов (b12 каталогируется на будущее, §7.7)
INDEX_BAND_KEYS: tuple[str, ...] = ("b02", "b03", "b04", "b05", "b08", "b11")

RESOLUTION_M = 10  # сетка B08; 20-м полосы ресемплятся в неё


@dataclass(frozen=True, slots=True)
class IndexStat:
    """Зональная статистика одного индекса одной сцены."""

    index: str
    index_min: float
    index_avg: float
    index_max: float
    cloud_pct: float


def read_band_into_grid(
    href: str,
    contour_geojson: dict,
    dst_transform,
    dst_crs,
    out_shape,
    *,
    resampling=Resampling.bilinear,
) -> np.ndarray:
    """Полоса COG (любой сетки/разрешения) в переданную сетку растров.

    Окно читается по своим границам контура и перепроецируется по
    координатам (как SCL в ndvi.py): непрерывные полосы — bilinear,
    SCL-классы — nearest. Вне плашки — 0 (NaN-индекс, отбрасывается маской).
    """
    ds = _open_cog(href)
    try:
        geom = transform_geom("EPSG:4326", ds.crs, contour_geojson)
        w = from_bounds(*shape(geom).bounds, transform=ds.transform)
        window = Window(
            col_off=math.floor(w.col_off),
            row_off=math.floor(w.row_off),
            width=math.ceil(w.col_off + w.width) - math.floor(w.col_off),
            height=math.ceil(w.row_off + w.height) - math.floor(w.row_off),
        )
        src = ds.read(1, window=window, boundless=True, fill_value=0)
        out = np.zeros(out_shape, dtype="float32")
        reproject(
            source=src,
            destination=out,
            src_transform=ds.window_transform(window),
            src_crs=ds.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=resampling,
        )
        return out
    finally:
        ds.close()


def compute_scene_indices(assets: dict, contour_geojson: dict) -> list[IndexStat]:
    """Статистика индексов EXTRA_INDICES по COG-активам сцены и контуру.

    Базовая сетка — окно B08 (10 м); полосы b02/b03/b04 читаются окнами
    (10 м, при несовпадении сетки — DataGapError как у NDVI), b05/b11 —
    перепроецируются в сетку B08 (bilinear). Индексы с отсутствующими
    полосами пропускаются (строки каталога до #128).
    """
    missing = [band for band in ("b04", "b08", "scl") if not assets.get(band)]
    if missing:
        raise ValueError(f"в активах сцены нет каналов: {', '.join(missing)}")

    nir_arr, nir_transform, nir_crs = _read_band_window(assets["b08"], contour_geojson)
    red_arr, red_transform, _ = _read_band_window(assets["b04"], contour_geojson)
    if red_arr.shape != nir_arr.shape or red_transform != nir_transform:
        raise ValueError("каналы b04/b08 на разных сетках — окно не совпадает")

    nir = nir_arr.astype("float32")
    red = red_arr.astype("float32")

    bands: dict[str, np.ndarray] = {"b04": red, "b08": nir}
    for key in ("b02", "b03", "b05", "b11"):
        href = assets.get(key)
        if not href:
            continue
        try:
            bands[key] = read_band_into_grid(
                href, contour_geojson, nir_transform, nir_crs, nir_arr.shape
            )
        except Exception:  # noqa: BLE001 — полоса вторична, индекс пропустим
            logger.warning("indices: полоса %s не прочитана — индексы с ней пропущены", key,
                           exc_info=True)

    scl = _read_scl_grid(assets, contour_geojson, nir_transform, nir_crs, nir_arr.shape)
    cloud = np.isin(scl, CLOUD_SCL_VALUES)

    geom_nir = transform_geom("EPSG:4326", nir_crs, contour_geojson)
    inside = contour_mask(geom_nir, nir_transform, nir_arr.shape)
    ndvi = compute_index_array("ndvi", nir, red)
    useful = inside & np.isfinite(ndvi)
    if not useful.any():
        raise ValueError("внутри контура нет пикселей с данными каналов")
    cloud_pct = round(100.0 * float((cloud & useful).sum()) / float(useful.sum()), 1)
    valid = useful & ~cloud
    if not valid.any():
        return []  # сцена целиком под облаком — её и не сохранят (05 §5)

    def _nd(name: str, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return compute_index_array(name, a, b)

    # EVI/SAVI: константы формул (6; 7,5; 1; L=0,5) рассчитаны на отражения
    # 0–1 — DN Sentinel-2 (0–10000) делим на 10000; ND* индексы
    # масштабоинвариантны (§7.7 #128)
    dn = 10000.0
    nir_s, red_s, blue_s = nir / dn, red / dn, bands.get("b02", np.zeros_like(nir)) / dn
    evi_lo, evi_hi = INDEX_BOUNDS.get("evi", (-1.0, 3.0))

    def _evi() -> np.ndarray | None:
        if "b02" not in bands:
            return None
        evi = compute_evi_array(nir_s, red_s, blue_s)
        # пиксели с вырожденным знаменателем (≈0) дают артефакты — отбрасываем
        return np.where((evi >= evi_lo) & (evi <= evi_hi), evi, np.nan).astype("float32")

    arrays: dict[str, np.ndarray | None] = {
        "ndwi": _nd("ndwi", bands["b03"], nir) if "b03" in bands else None,
        "ndre": _nd("ndre", nir, bands["b05"]) if "b05" in bands else None,
        "ndmi": _nd("ndmi", nir, bands["b11"]) if "b11" in bands else None,
        "ndsi": _nd("ndsi", bands["b03"], bands["b11"])
        if "b03" in bands and "b11" in bands
        else None,
        "evi": _evi(),
        "savi": _nd("savi", nir_s, red_s),
        "bsi": compute_bsi_array(nir, red, bands["b02"], bands["b11"])
        if "b02" in bands and "b11" in bands
        else None,
    }

    stats: list[IndexStat] = []
    for name in EXTRA_INDICES:
        arr = arrays[name]
        if arr is None:
            continue  # нет полосы в активах — норма для строк до #128
        pixels = arr[valid & np.isfinite(arr)]
        if pixels.size == 0:
            logger.info("indices: %s — нет валидных пикселей, пропуск", name)
            continue
        try:
            validate_index_values(name, pixels)
        except Exception:  # noqa: BLE001 — качество одного индекса не роняет сцену
            logger.warning("indices: %s не прошёл валидацию, пропуск", name, exc_info=True)
            continue
        index_min, index_avg, index_max = stats_over_mask(arr, valid)
        stats.append(IndexStat(name, index_min, index_avg, index_max, cloud_pct))
    return stats


def upsert_scene_indices(
    db: Session, field_id: uuid.UUID, captured_at: date, stats: list[IndexStat]
) -> int:
    """Записать сцены индексов (on_conflict_do_nothing); вернуть число новых."""
    inserted = 0
    for st in stats:
        statement = (
            pg_insert(SatelliteScene)
            .values(
                field_id=field_id,
                index=st.index,
                captured_at=captured_at,
                resolution_m=RESOLUTION_M,
                index_avg=st.index_avg,
                index_min=st.index_min,
                index_max=st.index_max,
                cloud_pct=st.cloud_pct,
                composite_days=1,
                source_id=SATELLITE_SOURCE_ID,
                trust=round(1.0 - st.cloud_pct / 100.0, 2),
            )
            .on_conflict_do_nothing(constraint="uq_dat_satellite_scene")
            .returning(SatelliteScene.id)
        )
        if db.execute(statement).first() is not None:
            inserted += 1
    return inserted


def missing_indices_for(field_id: uuid.UUID, captured_at: date, db: Session) -> list[str]:
    """Какие индексы EXTRA_INDICES ещё не записаны для (поле, дата) — идемпотентность."""
    present = set(
        db.scalars(
            select(SatelliteScene.index).where(
                SatelliteScene.field_id == field_id,
                SatelliteScene.captured_at == captured_at,
                SatelliteScene.index.in_(EXTRA_INDICES),
            )
        )
    )
    return [name for name in EXTRA_INDICES if name not in present]


__all__ = [
    "EXTRA_INDICES",
    "INDEX_BANDS_ASSETS",
    "INDEX_BAND_KEYS",
    "IndexStat",
    "compute_scene_indices",
    "missing_indices_for",
    "read_band_into_grid",
    "upsert_scene_indices",
]
