"""Бэкфилл индексов §7.7 по историческим сценам (#128, docs/06 §7.7).

Кандидаты — сцены ndvi (cloud ≤ 60), у которых не хватает сцен индексов
EXTRA_INDICES (ndwi/ndre/ndmi/ndsi/evi/savi/bsi) на ту же дату. Активы —
из строки dat_satellite_catalog (field_id, captured_at). Если в активах
нет полос b05/b11 (строки, каталогизированные до #128) и передан STAC-
клиент, активы дозапрашиваются поиском по item_id (окно ±1 сутки,
bbox хозяйства) и строка каталога обновляется — «докачка полос».

Расчёт — indices.compute_scene_indices (те же пиксели/облачность, что у
NDVI), запись — upsert по UQ, идемпотентно: повторный запуск ничего не
меняет (кандидатов без недостающих индексов просто нет).

Рендеры (render_dir): сцены, у которых уже есть PNG-рендеры, перерендери-
ваются с новыми вариантами индексов (§7.7); ошиб­ки рендера не влияют
на сцены. Недоступность COG — unavailable, повторный запуск доберёт.
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.integrations.stac import StacSearchClient
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.catalog import farm_bbox
from app.services.sentinel_ingest.indices import (
    EXTRA_INDICES,
    compute_scene_indices,
    upsert_scene_indices,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IndicesBackfillReport:
    """Итог бэкфилла индексов (CLI-отчёт JSON и тесты)."""

    scenes: int = 0  # сцен-кандидатов (не хватает индексов §7.7)
    scenes_written: int = 0  # сцен, где записан хотя бы один новый индекс
    index_scenes: int = 0  # всего записано сцен-индексов
    per_index: dict[str, int] = field(default_factory=dict)
    assets_refreshed: int = 0  # строк каталога с дозапрошенными полосами
    no_catalog: int = 0  # нет строки каталога (field_id, captured_at)
    partial_bands: int = 0  # сцен без полос b05/b11 (часть индексов пропущена)
    gaps: int = 0  # контура нет / данных нет
    failed: int = 0  # ошибки расчёта
    rendered: int = 0  # перерендерено сцен (новые варианты, §7.7)
    unavailable: list[str] = field(default_factory=list)  # COG недоступен — повтор

    def as_dict(self) -> dict:
        data = asdict(self)
        data["unavailable_count"] = len(self.unavailable)
        return data


def _refresh_assets(
    db: Session, client: StacSearchClient, bbox: list[float], row: SatelliteCatalogItem
) -> bool:
    """Дозапросить активы item'а из STAC (окно ±1 сутки) и обновить строку.

    True — строка обновлена (полосы b05/b11 появились). Ложь — item не
    найден/полос нет; не ошибка (индексы без полос пропустятся).
    """
    try:
        items = client.search(
            bbox,
            row.captured_at - timedelta(days=1),
            row.captured_at + timedelta(days=1),
        )
    except Exception:  # noqa: BLE001 — STAC недоступен: работаем с тем, что есть
        logger.warning("indices-backfill: STAC-дозапрос %s не удался", row.item_id, exc_info=True)
        return False
    for item in items:
        if item.item_id != row.item_id:
            continue
        merged = {**(row.assets or {}), **item.assets}
        if "b05" in merged and "b11" in merged:
            db.execute(
                update(SatelliteCatalogItem)
                .where(SatelliteCatalogItem.id == row.id)
                .values(assets=merged)
            )
            db.commit()
            return True
        break
    return False


def run_indices_backfill(
    db: Session,
    *,
    client: StacSearchClient | None = None,
    date_from: date | None = None,
    max_workers: int = 4,
    render_dir: str | None = None,
) -> IndicesBackfillReport:
    """Досчитать индексы §7.7 по историческим ndvi-сценам (идемпотентно)."""
    from app.modules.satellite.ingest import contour_lookup_factory
    from app.services.sentinel_ingest.renders import render_scene, renders_exist, write_renders

    report = IndicesBackfillReport(per_index={name: 0 for name in EXTRA_INDICES})

    # кандидаты: сцены ndvi без полного набора индексов §7.7
    criteria = [SatelliteScene.index == "ndvi", SatelliteScene.cloud_pct <= 60.0]
    if date_from is not None:
        criteria.append(SatelliteScene.captured_at >= date_from)
    ndvi_scenes = (
        db.execute(
            select(SatelliteScene.field_id, SatelliteScene.captured_at)
            .where(*criteria)
            .order_by(SatelliteScene.captured_at, SatelliteScene.field_id)
        )
        .all()
    )
    candidates = [
        (field_id, captured_at)
        for field_id, captured_at in ndvi_scenes
        if _missing_indices(db, field_id, captured_at)
    ]
    report.scenes = len(candidates)
    if not candidates:
        return report

    lookup = contour_lookup_factory(db)
    bbox = farm_bbox(db) if client is not None else None

    def compute_one(task: tuple[uuid.UUID, date]):
        field_id, captured_at = task
        contour = lookup(str(field_id))
        if contour is None:
            return task, None, ValueError(f"поле {field_id} без контура"), {}
        row = db.execute(
            select(SatelliteCatalogItem)
            .where(
                SatelliteCatalogItem.field_id == field_id,
                SatelliteCatalogItem.captured_at == captured_at,
            )
            .order_by(
                # предпочитаем строку с новыми полосами (меньше дозапросов)
                (SatelliteCatalogItem.assets["b05"].isnot(None)).desc()
            )
        ).scalars().first()
        if row is None:
            return task, None, ValueError("нет строки каталога"), {}
        assets = dict(row.assets or {})
        refreshed = False
        if client is not None and bbox and ("b05" not in assets or "b11" not in assets):
            refreshed = _refresh_assets(db, client, bbox, row)
            if refreshed:
                assets = dict(row.assets or {})  # обновлённые значения
        try:
            stats = compute_scene_indices(assets, contour)
        except Exception as exc:  # noqa: BLE001 — классифицируем в основном потоке
            return task, None, exc, {"refreshed": refreshed, "assets": assets}
        return task, stats, None, {"refreshed": refreshed, "assets": assets, "row": row}

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for task, stats, exc, extra in pool.map(compute_one, candidates):
            field_id, captured_at = task
            if exc is not None:
                name = type(exc).__name__
                if name == "SourceUnavailableError":
                    report.unavailable.append(f"{field_id}/{captured_at}: {exc}")
                elif name in ("DataGapError", "ValueError"):
                    report.gaps += 1 if "контура" not in str(exc) else report.gaps
                    if "строки каталога" in str(exc):
                        report.no_catalog += 1
                    else:
                        report.gaps += 1
                else:
                    report.failed += 1
                continue
            if extra.get("refreshed"):
                report.assets_refreshed += 1
            written = upsert_scene_indices(db, field_id, captured_at, stats or [])
            db.commit()
            if written:
                report.scenes_written += 1
                report.index_scenes += written
                for st in stats or []:
                    report.per_index[st.index] = report.per_index.get(st.index, 0) + 1
            if not stats:
                report.partial_bands += 1
            # перерендер с новыми вариантами (§7.7): только уже отрендеренные сцены
            if render_dir and stats:
                try:
                    if renders_exist(render_dir, field_id, captured_at):
                        assets = extra.get("assets") or {}
                        contour = lookup(str(field_id))
                        render = render_scene(assets, contour)
                        if render is not None:
                            write_renders(render_dir, field_id, captured_at, render)
                            report.rendered += 1
                except Exception:  # noqa: BLE001 — рендер вторичен
                    logger.warning(
                        "indices-backfill: рендер %s/%s не обновлён", field_id, captured_at,
                        exc_info=True,
                    )

    logger.info(
        "indices-backfill: кандидатов=%d сцен с индексами=%d строк-индексов=%d "
        "дозапросов активов=%d гэпов=%d ошибок=%d недоступно=%d",
        report.scenes,
        report.scenes_written,
        report.index_scenes,
        report.assets_refreshed,
        report.gaps,
        report.failed,
        len(report.unavailable),
    )
    return report


def _missing_indices(db: Session, field_id: uuid.UUID, captured_at: date) -> list[str]:
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


__all__ = ["IndicesBackfillReport", "run_indices_backfill"]
