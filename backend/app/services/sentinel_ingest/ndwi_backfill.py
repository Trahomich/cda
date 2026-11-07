"""Бэкфилл NDWI-статистики исторических сцен (v3-12, #104, docs/06 §7.4).

Сцены, рассчитанные до #91 (или по строкам каталога без b03), хранят
ndwi_mean/ndwi_p90 = NULL. Бэкфилл повторно выполняет зональную
статистику NDWI по COG-активам строки каталога (field_id, captured_at)
и дозаполняет колонки существующих сцен dat_satellite_scene (index='ndvi').

Расчёт идёт функцией zonal_ndvi (#51/#91) — набор пикселей и правила
облачности те же, что при живом ингесте; из результата берутся только
ndwi_mean/ndwi_p90 (NDVI-колонки и trust не пересчитываются — сцена уже
принята по этим данным). Идемпотентен: кандидаты — только сцены с
ndwi_p90 IS NULL; сцены каталога без канала b03 остаются NULL (no_b03).

Недоступность COG (сеть/5xx) — сцена не обновляется и попадает в
unavailable: повторный запуск доберёт её (SourceUnavailableError, 05 §6).
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.integrations import DataGapError, DataQualityError, SourceUnavailableError
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.ndvi import NDWI_BAND_ASSET, NdviStats, zonal_ndvi

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NdwiBackfillReport:
    """Итог бэкфилла (для CLI-отчёта JSON и тестов)."""

    scenes: int = 0  # сцен-кандидатов (ndvi, ndwi_p90 IS NULL, после date_from)
    updated: int = 0  # сцен обновлено (ndwi_mean/ndwi_p90 записаны)
    no_catalog: int = 0  # нет строки каталога (field_id, captured_at)
    no_b03: int = 0  # каталог без канала b03 — NDWI остаётся NULL (не ошибка)
    gaps: int = 0  # контура нет / данных нет / расчёт не дал NDWI
    failed: int = 0  # ошибки качества данных (DataQualityError и пр.)
    unavailable: list[str] = field(default_factory=list)  # COG недоступен — повтор

    def as_dict(self) -> dict:
        data = asdict(self)
        data["unavailable_count"] = len(self.unavailable)
        return data


def run_ndwi_backfill(
    db: Session,
    *,
    date_from: date | None = None,
    max_workers: int = 4,
) -> NdwiBackfillReport:
    """Дозаполнить ndwi_mean/ndwi_p90 исторических сцен по активам каталога.

    Кандидаты — сцены ndvi с ndwi_p90 IS NULL (и captured_at >= date_from,
    если задан). Активы берутся из строки dat_satellite_catalog с тем же
    (field_id, captured_at) — статусы каталога не фильтруются: наличие сцены
    уже означает успешную обработку; при нескольких item на дату предпочитается
    строка с b03. Чтение COG (сеть) — пулом потоков, запись — в основном.
    """
    from app.modules.satellite.ingest import contour_lookup_factory

    criteria = [SatelliteScene.index == "ndvi", SatelliteScene.ndwi_p90.is_(None)]
    if date_from is not None:
        criteria.append(SatelliteScene.captured_at >= date_from)
    scenes = (
        db.execute(
            select(SatelliteScene)
            .where(*criteria)
            .order_by(SatelliteScene.captured_at, SatelliteScene.field_id)
        )
        .scalars()
        .all()
    )
    report = NdwiBackfillReport(scenes=len(scenes))
    if not scenes:
        return report

    # активы каталога по (field_id, captured_at); при нескольких item на дату —
    # строка с b03 (остальные NDWI дать не могут)
    catalog: dict[tuple[uuid.UUID, date], dict] = {}
    for fid, cap, assets in db.execute(
        select(
            SatelliteCatalogItem.field_id,
            SatelliteCatalogItem.captured_at,
            SatelliteCatalogItem.assets,
        )
    ).all():
        key = (fid, cap)
        assets = dict(assets or {})
        if key not in catalog or (
            NDWI_BAND_ASSET in assets and NDWI_BAND_ASSET not in catalog[key]
        ):
            catalog[key] = assets

    lookup = contour_lookup_factory(db)
    contours: dict[uuid.UUID, dict | None] = {}
    tasks: list[tuple[uuid.UUID, dict, dict]] = []  # (scene_id, contour, assets)
    for scene in scenes:
        assets = catalog.get((scene.field_id, scene.captured_at))
        if assets is None:
            report.no_catalog += 1
            continue
        if not assets.get(NDWI_BAND_ASSET):
            report.no_b03 += 1
            continue
        if scene.field_id not in contours:
            contours[scene.field_id] = lookup(str(scene.field_id))
        contour = contours[scene.field_id]
        if contour is None:
            report.gaps += 1
            continue
        tasks.append((scene.id, contour, assets))
    db.rollback()  # закрыть неявную транзакцию select'ов перед записями

    def compute(task: tuple[uuid.UUID, dict, dict]) -> tuple[uuid.UUID, NdviStats | BaseException]:
        scene_id, contour, assets = task
        try:
            stats = zonal_ndvi(assets, contour)
            if stats.ndwi_mean is None or stats.ndwi_p90 is None:
                # b03 есть, но NDWI не считался (сетка/пиксели) — сцена остаётся NULL
                return scene_id, DataGapError("NDWI не рассчитан по каналу b03")
            return scene_id, stats
        except (DataGapError, DataQualityError, SourceUnavailableError) as exc:
            return scene_id, exc

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for scene_id, result in pool.map(compute, tasks):
            done += 1
            if isinstance(result, SourceUnavailableError):
                report.unavailable.append(f"{scene_id}: {result}")
                db.rollback()
                continue
            if isinstance(result, BaseException):
                db.rollback()
                if isinstance(result, DataGapError):
                    report.gaps += 1
                else:
                    report.failed += 1
                continue
            db.execute(
                update(SatelliteScene)
                .where(SatelliteScene.id == scene_id)
                .values(
                    ndwi_mean=result.ndwi_mean,
                    ndwi_p90=result.ndwi_p90,
                    updated_at=datetime.now(tz=UTC),
                )
            )
            db.commit()
            report.updated += 1
            if done % 50 == 0:
                logger.info(
                    "sentinel-ndwi-backfill: %d/%d кандидатов, обновлено %d",
                    done,
                    len(tasks),
                    report.updated,
                )

    if report.unavailable:
        logger.error(
            "sentinel-ndwi-backfill: COG недоступны для %d сцен (повторный запуск доберёт)",
            len(report.unavailable),
        )
    logger.info(
        "sentinel-ndwi-backfill: кандидатов=%d обновлено=%d без каталога=%d без b03=%d "
        "гэпов=%d ошибок=%d недоступно=%d",
        report.scenes,
        report.updated,
        report.no_catalog,
        report.no_b03,
        report.gaps,
        report.failed,
        len(report.unavailable),
    )
    return report


__all__ = ["NdwiBackfillReport", "run_ndwi_backfill"]
