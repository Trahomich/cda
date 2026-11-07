"""sentinel-ingest — сервис каталогизации сцен Sentinel-2 (v1-04, ADR-09).

Отдельный контейнер-воркер без внешнего API (ADR-09, docs/02 §8), общий
каркас ingest-сервисов с weather-ingest: env по CONTRACTS.md §7, точка входа
python -m app.services.sentinel_ingest. По расписанию docs/05 §4 (каталог
раз в 6 ч, окно backfill 14 суток) ищет сцены sentinel-2-l2a в STAC-каталоге
earth-search (анонимно, без ключей) по bbox активных полей хозяйства,
сопоставляет footprint плашки контурам полей (PostGIS ST_Intersects) и
каталогизирует метаданные сцены (item_id, tile, cloud_pct, ссылки COG B04/
B08/SCL) в dat_satellite_catalog (новые строки — status="cataloged").
Затем этап NDVI (v1-05, #51, ndvi.py): строки cataloged → windowed read
COG по HTTP → NDVI (B08−B04)/(B08+B04) с маской облаков SCL ∈ {8,9,10}
→ зональная статистика в dat_satellite_scene, строка каталога →
processed (или failed для DataGap). Правила облачности — docs/05 §5 п.2
по честной облачности SCL полезной площади: ≤20 обычная сцена, 20–60
сохраняется (серым в таймлайне), >60 не сохраняется вовсе.

Облачность: каталогизация до 60 % (eo:cloud_cover плашки, 05 §5 п.2);
честная облачность по маске SCL полезной площади пересчитается на этапе NDVI.

Состояние источника (API-28, REQ-F-034): dat_source_journal +
ref_source.last_status/last_success_at для source_id="satellite.sentinel2-l2a";
ок/облачная сцена — ok, нет сцен за окно/нет полей — degraded, недоступность
STAC (5xx/сеть) — error, повтор по расписанию (05 §4/§6).

Джобы (env, CONTRACTS.md §7):
- catalog — INGEST_SATELLITE_CATALOG_HOURS (default 6, 05 §4): поиск и
  каталогизация сцен за окно INGEST_SATELLITE_BACKFILL_DAYS (default 14);
- ndvi — сразу после каталога в том же цикле (05 §2 v1: «при появлении новой
  сцены — вычисление индексов»): расчёт по строкам cataloged, пул
  INGEST_SATELLITE_NDVI_WORKERS потоков, батч INGEST_SATELLITE_NDVI_BATCH;
- ретраи при недоступности — INGEST_SATELLITE_RETRY_ATTEMPTS×
  INGEST_SATELLITE_RETRY_BASE_SECONDS внутри цикла, далее — планировщик.

Общее: INGEST_RUN_ONCE=true — один цикл и выход (стенд/тесты).
Запуск: python -m app.services.sentinel_ingest
"""

import logging
import time
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from app.core.config import Settings
from app.core.db import SessionLocal
from app.integrations.stac import EarthSearchStacClient
from app.services.sentinel_ingest.catalog import run_catalog_cycle
from app.services.sentinel_ingest.ndvi import run_ndvi_cycle

logger = logging.getLogger(__name__)

JOB_CATALOG = "sentinel_catalog"


def catalog_window(settings: Settings) -> tuple[date, date]:
    """Окно поиска: backfill суток назад до сегодня (05 §2 backfill)."""
    today = datetime.now(tz=UTC).date()
    return today - timedelta(days=settings.ingest_satellite_backfill_days), today


def run_once(settings: Settings) -> None:
    """Один цикл воркера: каталогизация (с ретраями), затем расчёт NDVI.

    Ретраи каталога (05 §4/§6): при недоступности источника повтор с
    экспоненциальной паузой INGEST_SATELLITE_RETRY_BASE_SECONDS×2^n; после
    исчерпания — ошибка уже записана в журнал, цикл повторит планировщик.
    Расчёт NDVI устойчив к ошибкам сам (ndvi.py): недоступность COG —
    журнал error, строки ждут следующего цикла.
    """
    _catalog_attempts(settings)
    _ndvi_cycle(settings)


def _catalog_attempts(settings: Settings) -> None:
    attempts = max(1, settings.ingest_satellite_retry_attempts)
    date_from, date_to = catalog_window(settings)
    for attempt in range(1, attempts + 1):
        db = SessionLocal()
        try:
            client = EarthSearchStacClient(
                base_url=settings.stac_api_url,
                request_timeout=settings.ingest_satellite_request_timeout,
            )
            report = run_catalog_cycle(db, client, date_from=date_from, date_to=date_to)
            logger.info(
                "sentinel-ingest [catalog]: сцен=%d полей=%d новых=%d status=%s",
                report.scenes,
                report.fields,
                report.new_rows,
                report.status,
            )
            if report.status != "error":
                return
        except Exception:  # noqa: BLE001 — фоновая джоба не роняет воркер
            logger.exception("sentinel-ingest [catalog]: сбой прогона")
        finally:
            db.close()
        if attempt < attempts:
            pause = settings.ingest_satellite_retry_base_seconds * 2 ** (attempt - 1)
            if pause > 0:
                logger.warning(
                    "sentinel-ingest [catalog]: попытка %d/%d не удалась, пауза %.0f с",
                    attempt,
                    attempts,
                    pause,
                )
                time.sleep(pause)


def _ndvi_cycle(settings: Settings) -> None:
    """Цикл расчёта NDVI по каталогизированным сценам (#51, 05 §2 v1)."""
    db = SessionLocal()
    try:
        run_ndvi_cycle(
            db,
            batch_limit=settings.ingest_satellite_ndvi_batch,
            max_workers=settings.ingest_satellite_ndvi_workers,
            render_dir=settings.ndvi_render_dir or None,
        )
    except Exception:  # noqa: BLE001 — фоновая джоба не роняет воркер
        logger.exception("sentinel-ingest [ndvi]: сбой прогона")
    finally:
        db.close()


def create_worker(settings: Settings) -> BlockingScheduler:
    """Планировщик: каталог каждые INGEST_SATELLITE_CATALOG_HOURS часов.

    Первый прогон — сразу при старте (backfill-окно закрывает пропуски).
    """
    scheduler = BlockingScheduler(timezone=UTC)
    scheduler.add_job(
        run_once,
        args=(settings,),
        trigger="interval",
        hours=settings.ingest_satellite_catalog_hours,
        id=JOB_CATALOG,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(tz=UTC),
    )
    return scheduler


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "sentinel-ingest: старт (catalog=%d ч, backfill=%d сут, stac=%s, run_once=%s)",
        settings.ingest_satellite_catalog_hours,
        settings.ingest_satellite_backfill_days,
        settings.stac_api_url,
        settings.ingest_run_once,
    )
    if settings.ingest_run_once:
        run_once(settings)
        logger.info("sentinel-ingest: режим INGEST_RUN_ONCE — цикл выполнен, выход")
        return
    scheduler = create_worker(settings)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("sentinel-ingest: остановка по сигналу")


if __name__ == "__main__":
    main()
