"""Расчёт NDVI по каталогизированным сценам Sentinel-2 (v1-05, #51, docs/05 §2).

Этап v1 после каталогизации (#50): строки dat_satellite_catalog со
status="cataloged" → windowed read COG-активов по HTTP (b04/b08/scl, без
скачивания сцен), NDVI = (B08 − B04)/(B08 + B04), маскирование облаков
SCL ∈ {8, 9, 10}, зональная статистика по контуру поля. Результат —
dat_satellite_scene (index="ndvi", 10 м); строки каталога переводятся в
processed/failed.

Правила облачности (docs/05 §5 п.2) — по честной облачности SCL полезной
площади поля, а не по eo:cloud_cover плашки:
- cloud_pct ≤ 20 — обычная сцена;
- 20 < cloud_pct ≤ 60 — сцена сохраняется (в таймлайне серым);
- cloud_pct > 60 — не сохраняется вовсе (строка каталога processed, сцены нет).

trust = 1 − cloud_pct/100 (одиночная сцена, docs/06 §4).
std статистики — для внутреннего контроля (в логах, без колонки в БД).

Ошибки источника (COG недоступен: 5xx/сеть) → строка остаётся cataloged,
повтор в следующем цикле планировщика; в журнал API-28 — error. DataGap
(нет валидных пикселей/активов, поле вне плашки) — перманентный: строка
failed с причиной. Воркер не падает.
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations import DataGapError, DataQualityError, SourceUnavailableError
from app.integrations.raster import (
    compute_index_array,
    contour_mask,
    stats_over_mask,
    validate_index_values,
)
from app.integrations.stac import SATELLITE_SOURCE_ID
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.catalog import log_source

logger = logging.getLogger(__name__)

#: SCL-метки облаков и снега (docs/05 §2 v1: 8=cloud medium, 9=cloud high, 10=cirrus)
CLOUD_SCL_VALUES = (8, 9, 10)

#: индекс и разрешение растра Sentinel-2 L2A (docs/03, ENT-SATELLITE-SCENE)
INDEX = "ndvi"
RESOLUTION_M = 10

#: порог «сцена не сохраняется» (05 §5 п.2)
MAX_SCENE_CLOUD_PCT = 60.0

#: ключи активов каталога (канонические, маппинг ESA-имён — STAC-клиент #50)
BAND_ASSETS = ("b04", "b08", "scl")


@dataclass(frozen=True, slots=True)
class NdviStats:
    """Зональная статистика NDVI одного поля по одной сцене."""

    index_min: float
    index_avg: float
    index_max: float
    std: float  # внутренний контроль (в БД не пишется)
    cloud_pct: float


@dataclass(slots=True)
class NdviReport:
    """Итог цикла расчёта NDVI (для журналов и тестов)."""

    rows: int = 0  # обработано строк каталога
    scenes: int = 0  # записано сцен dat_satellite_scene
    cloud_skipped: int = 0  # честная облачность > 60 — не сохранена (05 §5 п.2)
    gaps: int = 0  # DataGap — строка failed (данных нет, повтор бессмыслен)
    failed: int = 0  # ошибки расчёта (DataQualityError и пр.) — failed
    unavailable: list[str] = field(default_factory=list)  # COG недоступен — повтор
    skipped: str | None = None  # причина пропуска цикла целиком

    @property
    def status(self) -> str:
        if self.unavailable:
            return "error"
        if self.skipped:
            return "skipped"
        if self.rows == 0:
            return "idle"  # каталога обрабатывать нечего — источник ни при чём
        if self.scenes == 0:
            return "degraded"  # обрабатывали, но ни одной сцены не записали
        return "ok"


def _open_cog(href: str):
    """Открытие COG по HTTP (или пути); недоступность → SourceUnavailableError."""
    import rasterio

    try:
        return rasterio.open(href)
    except Exception as exc:  # RasterioIOError: 5xx/сеть/битая ссылка
        raise SourceUnavailableError(f"COG недоступен: {href}: {exc}"[:300]) from exc


def _read_band_window(href: str, contour_geojson: dict) -> tuple[np.ndarray, object, object]:
    """(массив, transform, crs) окна растра под контуром (windowed read).

    Контур WGS84 перепроцируется в CRS канала; читается только окно bounds
    контура (сцена не скачивается). Вне плашки — fill 0: для отражений он
    даёт NaN-индекс (исключается из статистики), для SCL 0 = nodata.
    """
    from rasterio.warp import transform_geom
    from rasterio.windows import from_bounds
    from shapely.geometry import shape

    ds = _open_cog(href)
    try:
        geom_raster = transform_geom("EPSG:4326", ds.crs, contour_geojson)
        bounds = shape(geom_raster).bounds
        window = from_bounds(*bounds, transform=ds.transform)
        if (
            window.width <= 0
            or window.height <= 0
            or window.col_off >= ds.width
            or window.row_off >= ds.height
            or window.col_off + window.width <= 0
            or window.row_off + window.height <= 0
        ):
            raise DataGapError(f"поле вне плашки канала: {href}")
        arr = ds.read(1, window=window, boundless=True, fill_value=0)
        return arr, ds.window_transform(window), ds.crs
    finally:
        ds.close()


def zonal_ndvi(assets: dict, contour_geojson: dict) -> NdviStats:
    """Зональная статистика NDVI поля по COG-активам сцены (b04/b08/scl).

    B04/B08 — сетка 10 м; SCL (20 м) читается в сетку B08 (nearest).
    cloud_pct — доля пикселей SCL ∈ {8,9,10} полезной площади (пиксели внутри
    контура с данными каналов), docs/05 §2 v1. При облачности > 60 статистика
    не считается (сцене всё равно не сохраняться, 05 §5 п.2).
    """
    from rasterio.enums import Resampling
    from rasterio.warp import transform_geom
    from rasterio.windows import from_bounds
    from shapely.geometry import shape

    missing = [band for band in BAND_ASSETS if not assets.get(band)]
    if missing:
        raise DataGapError(f"в активах сцены нет каналов: {', '.join(missing)}")

    red_arr, red_transform, red_crs = _read_band_window(assets["b04"], contour_geojson)
    nir_arr, nir_transform, nir_crs = _read_band_window(assets["b08"], contour_geojson)
    if red_arr.shape != nir_arr.shape or red_transform != nir_transform:
        raise DataGapError("каналы b04/b08 на разных сетках — окно не совпадает")

    # SCL → сетка NIR (10 м, nearest), вне плашки — 0 (nodata, не облако)
    scl_ds = _open_cog(assets["scl"])
    try:
        geom_scl = transform_geom("EPSG:4326", scl_ds.crs, contour_geojson)
        window = from_bounds(*shape(geom_scl).bounds, transform=scl_ds.transform)
        scl = scl_ds.read(
            1,
            window=window,
            out_shape=nir_arr.shape,
            boundless=True,
            fill_value=0,
            resampling=Resampling.nearest,
        )
    finally:
        scl_ds.close()

    ndvi = compute_index_array(INDEX, nir_arr.astype("float32"), red_arr.astype("float32"))

    # маска контура — в CRS каналов (Sentinel-2 — UTM, поле — WGS84)
    geom_nir = transform_geom("EPSG:4326", nir_crs, contour_geojson)
    inside = contour_mask(geom_nir, nir_transform, nir_arr.shape)
    useful = inside & np.isfinite(ndvi)
    if not useful.any():
        raise DataGapError("внутри контура нет пикселей с данными каналов")

    cloud = np.isin(scl, CLOUD_SCL_VALUES)
    cloud_pct = round(100.0 * float((cloud & useful).sum()) / float(useful.sum()), 1)
    if cloud_pct > MAX_SCENE_CLOUD_PCT:
        return NdviStats(0.0, 0.0, 0.0, 0.0, cloud_pct)

    valid = useful & ~cloud
    if not valid.any():
        raise DataGapError("полезная площадь полностью под облаком (SCL)")
    validate_index_values(INDEX, ndvi[valid])
    index_min, index_avg, index_max = stats_over_mask(ndvi, valid)
    std = round(float(np.std(ndvi[valid])), 3)
    return NdviStats(index_min, index_avg, index_max, std, cloud_pct)


def scene_upsert(
    db: Session, field_id: uuid.UUID, captured_at: date, stats: NdviStats
) -> bool:
    """Запись сцены ndvi; False, если (field, index, дата, источник) уже есть."""
    statement = (
        pg_insert(SatelliteScene)
        .values(
            field_id=field_id,
            index=INDEX,
            captured_at=captured_at,
            resolution_m=RESOLUTION_M,
            index_avg=stats.index_avg,
            index_min=stats.index_min,
            index_max=stats.index_max,
            cloud_pct=stats.cloud_pct,
            composite_days=1,
            source_id=SATELLITE_SOURCE_ID,
            trust=round(1.0 - stats.cloud_pct / 100.0, 2),
        )
        .on_conflict_do_nothing(constraint="uq_dat_satellite_scene")
        .returning(SatelliteScene.id)
    )
    return db.execute(statement).first() is not None


def _finish(item: SatelliteCatalogItem, status: str, error: str | None) -> None:
    item.status = status
    item.error = error[:500] if error else None
    item.processed_at = datetime.now(tz=UTC)


def apply_row_result(db: Session, item: SatelliteCatalogItem, result) -> str:
    """Готовый результат zonal_ndvi (или исключение) → запись БД, исход.

    'scene' — сцена записана; 'cloud' — >60 %, не сохранена; 'gap'/'failed' —
    строка каталога failed с причиной. SourceUnavailableError пробрасывается
    (строка остаётся cataloged — повтор следующим циклом).
    """
    if isinstance(result, BaseException):
        if isinstance(result, SourceUnavailableError):
            raise result
        _finish(item, "failed", str(result))
        return "gap" if isinstance(result, DataGapError) else "failed"

    stats: NdviStats = result
    if stats.cloud_pct > MAX_SCENE_CLOUD_PCT:
        _finish(item, "processed", None)
        logger.info(
            "sentinel-ndvi: %s поле %s — облачность %.1f%% > %.0f, сцена не сохранена",
            item.item_id,
            item.field_id,
            stats.cloud_pct,
            MAX_SCENE_CLOUD_PCT,
        )
        return "cloud"

    inserted = scene_upsert(db, item.field_id, item.captured_at, stats)
    _finish(item, "processed", None)
    logger.info(
        "sentinel-ndvi: %s поле %s — ndvi avg %.3f (min %.3f, max %.3f, std %.3f), "
        "cloud %.1f%%, trust %.2f%s",
        item.item_id,
        item.field_id,
        stats.index_avg,
        stats.index_min,
        stats.index_max,
        stats.std,
        stats.cloud_pct,
        1.0 - stats.cloud_pct / 100.0,
        "" if inserted else " (сцена уже была)",
    )
    return "scene"


def process_catalog_row(db: Session, item: SatelliteCatalogItem, contour: dict) -> str:
    """Полный путь одной строки каталога: расчёт + запись (без учёта пулов)."""
    return apply_row_result(db, item, zonal_ndvi(item.assets or {}, contour))


def run_ndvi_cycle(
    db: Session, *, batch_limit: int = 200, max_workers: int = 4
) -> NdviReport:
    """Цикл расчёта: строки каталога status='cataloged' → NDVI → сцены.

    Чтение COG (сеть) выполняется пулом потоков (rasterio отпускает GIL),
    запись в БД — в основном потоке сессией. Прогресс — в логах; воркер
    не падает: недоступность COG — журнал error, строка ждёт повтора.
    """
    from app.modules.satellite.ingest import contour_lookup_factory

    rows = (
        db.execute(
            select(SatelliteCatalogItem)
            .where(SatelliteCatalogItem.status == "cataloged")
            .order_by(SatelliteCatalogItem.captured_at)
            .limit(batch_limit)
        )
        .scalars()
        .all()
    )
    if not rows:
        return NdviReport(skipped="нет сцен со статусом cataloged")

    lookup = contour_lookup_factory(db)
    contours: dict[uuid.UUID, dict | None] = {}
    for row in rows:
        if row.field_id not in contours:
            contours[row.field_id] = lookup(str(row.field_id))
    db.rollback()  # закрыть неявную транзакцию select'ов перед записями

    def compute(item: SatelliteCatalogItem) -> tuple[SatelliteCatalogItem, object]:
        contour = contours.get(item.field_id)
        if contour is None:
            return item, DataGapError(f"поле {item.field_id} неактивно или без контура")
        try:
            return item, zonal_ndvi(item.assets or {}, contour)
        except (DataGapError, DataQualityError, SourceUnavailableError) as exc:
            return item, exc

    report = NdviReport(rows=len(rows))
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for item, result in pool.map(compute, rows):
            try:
                outcome = apply_row_result(db, item, result)
            except SourceUnavailableError as exc:
                report.unavailable.append(f"{item.item_id}: {exc}")
                db.rollback()
                continue
            db.commit()
            if outcome == "scene":
                report.scenes += 1
            elif outcome == "cloud":
                report.cloud_skipped += 1
            elif outcome == "gap":
                report.gaps += 1
            else:
                report.failed += 1

    if report.unavailable:
        reason = "; ".join(report.unavailable)[:500]
        logger.error(
            "sentinel-ndvi: недоступны COG (%d строк): %s", len(report.unavailable), reason
        )
        log_source(db, "error", reason)
    elif report.status == "ok":
        log_source(db, "ok", None)
    elif report.status == "degraded":
        log_source(db, "degraded", "ни одной сцены не записано")
    logger.info(
        "sentinel-ingest [ndvi]: строк=%d сцен=%d облако>60=%d гэпов=%d ошибок=%d "
        "недоступно=%d status=%s",
        report.rows,
        report.scenes,
        report.cloud_skipped,
        report.gaps,
        report.failed,
        len(report.unavailable),
        report.status,
    )
    return report


__all__ = [
    "BAND_ASSETS",
    "CLOUD_SCL_VALUES",
    "INDEX",
    "MAX_SCENE_CLOUD_PCT",
    "NdviReport",
    "NdviStats",
    "RESOLUTION_M",
    "apply_row_result",
    "process_catalog_row",
    "run_ndvi_cycle",
    "scene_upsert",
    "zonal_ndvi",
]
