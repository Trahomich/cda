"""Бэкфилл исторических NDVI-сцен Sentinel-2 (#108, docs/06 §7.5).

Инжест прошлых сезонов тем же конвейером sentinel-ingest (ADR-09):
каталогизация STAC (run_catalog_cycle) → расчёт NDVI+NDWI и рендеры
ndvi/truecolor/ndwi (run_ndvi_cycle, §7.3/§7.4). Годы: current_year−N+1 …
current_year включительно (--years N, по умолчанию 4); поиск каталога —
окнами по кварталам (ответы STAC маленькие, журнал источников по окну).

Идемпотентность — UQ каталога (field_id, item_id) и UQ сцен
(field_id, index, captured_at, source_id): повторный прогон ничего не
добавляет. Item'ы без полного набора каналов (b02/b03/b04/b08/scl —
рендеры truecolor и NDWI) пропускаются с журналированием (assets_skipped),
не падая. Недоступность STAC/COG — окно/строка пропускаются и попадают
в отчёт (catalog_failed / scenes_unavailable): повторный запуск доберёт.

После инжеста инвалидируется кэш затронутых активных полей (§7.5):
insights:{field_id}:* и vra:peak:{field_id}[:{year}] — как при обычном
пересчёте, чтобы сезонные API (#110) не отдавали старые данные.

Запуск (стенд): python -m app.cli backfill-history --years 4 —
см. докстринг CLI и deploy/README.
"""

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import cache_delete
from app.services.sentinel_ingest.catalog import run_catalog_cycle
from app.services.sentinel_ingest.ndvi import run_ndvi_cycle

logger = logging.getLogger(__name__)

#: каналы, обязательные для исторической сцены: NDVI+маска (b04/b08/scl),
#: NDWI и рендер truecolor (b03/b02) — без них item пропускается (#108)
REQUIRED_BANDS = ("b02", "b03", "b04", "b08", "scl")


@dataclass(slots=True)
class HistoryBackfillReport:
    """Итог бэкфилла (для CLI-отчёта JSON и тестов)."""

    years: list[int] = field(default_factory=list)  # годы инжеста
    fields: int = 0  # активных полей (затронутых инвалидацей кэша)
    windows: int = 0  # квартальных окон каталога
    catalog_scenes: int = 0  # item'ов, найденных в STAC (после фильтра каналов)
    catalog_new_rows: int = 0  # новых строк dat_satellite_catalog
    assets_skipped: int = 0  # item'ов без полного набора каналов — пропуск
    catalog_failed: list[str] = field(default_factory=list)  # окна с недоступным STAC
    scenes_processed: int = 0  # строк каталога доведено до processed/failed
    scenes_cloud_skipped: int = 0  # сцен с честной облачностью > 60 (не сохранены)
    scenes_gaps: int = 0  # DataGap: контур вне плашки и т.п.
    scenes_failed: int = 0  # ошибки качества данных
    scenes_unavailable: list[str] = field(default_factory=list)  # COG недоступен — повтор
    per_year: dict[int, int] = field(default_factory=dict)  # НОВЫХ ndvi-сцен по годам
    cache_deleted: int = 0  # кэш-ключей инвалидировано

    def as_dict(self) -> dict:
        data = asdict(self)
        data["catalog_failed_count"] = len(data["catalog_failed"])
        data["scenes_unavailable_count"] = len(data["scenes_unavailable"])
        return data


class _FullAssetsFilter:
    """Обёртка STAC-клиента: исторические item'ы без b02/b03/b04/b08/scl
    пропускаются (счётчик — в отчёт), чтобы у каждой сцены были рендеры
    truecolor и статистика NDWI (§7.5)."""

    source_id: str  # делегируется внутреннему клиенту при необходимости

    def __init__(self, inner) -> None:
        self._inner = inner
        self.skipped = 0

    def search(self, bbox, date_from: date, date_to: date, *, max_cloud_pct=None):
        items = self._inner.search(bbox, date_from, date_to, max_cloud_pct=max_cloud_pct)
        kept = [i for i in items if all(i.assets.get(band) for band in REQUIRED_BANDS)]
        self.skipped += len(items) - len(kept)
        return kept


def quarter_windows(year: int, today: date) -> list[tuple[date, date]]:
    """Квартальные окна поиска каталога в году; будущее обрезано today."""
    windows: list[tuple[date, date]] = []
    for quarter in range(4):
        start = date(year, quarter * 3 + 1, 1)
        end_month = quarter * 3 + 3
        end = (
            date(year, 12, 31)
            if end_month == 12
            else date(end_month + 1, 1, 1) - timedelta(days=1)
        )
        if start > today:
            break
        if end > today:
            end = today
        windows.append((start, end))
    return windows


def _scene_counts_by_year(db: Session, year_from: int, year_to: int) -> dict[int, int]:
    rows = db.execute(
        text(
            "SELECT extract(year from captured_at)::int AS y, count(*) "
            "FROM dat_satellite_scene WHERE index = 'ndvi' "
            "AND extract(year from captured_at) BETWEEN :y1 AND :y2 GROUP BY 1"
        ),
        {"y1": year_from, "y2": year_to},
    ).fetchall()
    return {int(row[0]): int(row[1]) for row in rows}


def _active_field_ids(db: Session) -> list[str]:
    return [str(row[0]) for row in db.execute(text("SELECT id FROM dat_field WHERE status = 'active'"))]


def run_history_backfill(
    db: Session,
    *,
    client,
    years: int = 4,
    batch_limit: int = 200,
    max_workers: int = 4,
    render_dir: str | None = None,
    today: date | None = None,
) -> HistoryBackfillReport:
    """Инжест исторических NDVI-сцен прошлых сезонов (docs/06 §7.5).

    client — STAC-клиент (EarthSearchStacClient на стене, FakeStacClient в
    тестах); years — глубина в годах, включая текущий (4 → 2023–2026 при
    current_year=2026). Повторный прогон идемпотентен (UQ каталога и сцен).
    """
    today = today or date.today()
    year_to = today.year
    year_from = year_to - max(1, years) + 1
    report = HistoryBackfillReport(years=list(range(year_from, year_to + 1)))
    filtered_client = _FullAssetsFilter(client)
    before = _scene_counts_by_year(db, year_from, year_to)

    # 1) каталогизация: по квартальным окнам каждого года (§7.5)
    for year in report.years:
        for start, end in quarter_windows(year, today):
            catalog = run_catalog_cycle(db, filtered_client, date_from=start, date_to=end)
            report.windows += 1
            report.catalog_scenes += catalog.scenes
            report.catalog_new_rows += catalog.new_rows
            report.catalog_failed.extend(catalog.failed)
            if catalog.failed:
                logger.warning("backfill-history: окно %s..%s пропущено: %s", start, end, catalog.failed)
    report.assets_skipped = filtered_client.skipped

    # 2) расчёт NDVI/NDWI + рендеры по cataloged-строкам, батчами до исчерпания
    while True:
        ndvi = run_ndvi_cycle(
            db, batch_limit=batch_limit, max_workers=max_workers, render_dir=render_dir
        )
        report.scenes_processed += ndvi.scenes + ndvi.cloud_skipped + ndvi.gaps + ndvi.failed
        report.scenes_cloud_skipped += ndvi.cloud_skipped
        report.scenes_gaps += ndvi.gaps
        report.scenes_failed += ndvi.failed
        report.scenes_unavailable.extend(ndvi.unavailable)
        progress = ndvi.scenes + ndvi.cloud_skipped + ndvi.gaps + ndvi.failed
        if ndvi.rows < batch_limit or progress == 0:
            break  # всё обработано, либо строки не двигаются (недоступность COG)

    # 3) кэш затронутых полей: сезонные API (#110) не должны отдавать старое
    field_ids = _active_field_ids(db)
    for field_id in field_ids:
        report.cache_deleted += cache_delete(
            f"insights:{field_id}:*", f"vra:peak:{field_id}", f"vra:peak:{field_id}:*"
        )
    report.fields = len(field_ids)

    after = _scene_counts_by_year(db, year_from, year_to)
    report.per_year = {
        year: after.get(year, 0) - before.get(year, 0) for year in report.years
    }
    logger.info(
        "backfill-history: годы=%s окон=%d каталог: сцен=%d новых=%d без каналов=%d ошибок=%d; "
        "сцен новых=%d (по годам=%s), облако>60=%d гэпов=%d ошибок=%d недоступно=%d; "
        "кэш удалён=%d",
        f"{report.years[0]}–{report.years[-1]}",
        report.windows,
        report.catalog_scenes,
        report.catalog_new_rows,
        report.assets_skipped,
        len(report.catalog_failed),
        sum(report.per_year.values()),
        report.per_year,
        report.scenes_cloud_skipped,
        report.scenes_gaps,
        report.scenes_failed,
        len(report.scenes_unavailable),
        report.cache_deleted,
    )
    return report


__all__ = [
    "REQUIRED_BANDS",
    "HistoryBackfillReport",
    "quarter_windows",
    "run_history_backfill",
]
