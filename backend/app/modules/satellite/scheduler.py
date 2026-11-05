"""Фоновый опрос каталога Sentinel-2 (05 §4: каждые 6 ч; задача MVP-10).

Включается настройкой satellite_enabled (по умолчанию false: без ключей
Copernicus источник недоступен, тесты и локальная разработка наружу не ходят).
Джоба — SatelliteIngest.ingest_window по окну satellite_lookback_days;
ретраи выполняет ingest-слой (satellite_retry_*). Хранилище джоб — Redis
(несколько воркеров uvicorn), при недоступности — in-memory.
"""

import logging
from datetime import UTC, datetime

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import Settings
from app.core.db import SessionLocal
from app.integrations.sentinel2 import Sentinel2Adapter
from app.modules.satellite.ingest import SatelliteIngest, contour_lookup_factory

logger = logging.getLogger(__name__)


def _run_job(settings: Settings) -> None:
    """Прогон ingest с собственной сессией БД (вызывается планировщиком)."""
    db = SessionLocal()
    try:
        adapter = Sentinel2Adapter(
            geometry_lookup=contour_lookup_factory(db),
            auth_url=settings.copernicus_auth_url,
            search_url=settings.copernicus_search_url,
            client_id=settings.copernicus_client_id,
            client_secret=settings.copernicus_client_secret,
            request_timeout=settings.satellite_request_timeout,
            raster_cache_dir=settings.satellite_raster_cache_dir,
        )
        ingest = SatelliteIngest(
            adapter,
            indices=tuple(s.strip() for s in settings.satellite_indices.split(",") if s.strip()),
            lookback_days=settings.satellite_lookback_days,
            max_cloud_pct=settings.satellite_max_cloud_pct,
            retry_attempts=settings.satellite_retry_attempts,
            retry_base_seconds=settings.satellite_retry_base_seconds,
        )
        report = ingest.ingest_window(db)
        logger.info(
            "satellite poll: scenes=%d skipped=%d failed=%d status=%s",
            report.ok,
            report.skipped,
            len(report.failed),
            report.status,
        )
    except Exception:  # noqa: BLE001 — фоновая джоба не должна ронять планировщик
        logger.exception("satellite poll: сбой прогона")
    finally:
        db.close()


def create_satellite_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """Планировщик опроса каталога или None, если спутник выключен настройками."""
    if not settings.satellite_enabled:
        logger.info("опрос каталога Sentinel-2 выключен (satellite_enabled=false)")
        return None

    jobstores: dict[str, object] = {}
    try:
        jobstores["default"] = RedisJobStore(
            host=settings.redis_host, port=settings.redis_port, db=settings.redis_db
        )
    except Exception as exc:  # noqa: BLE001 — Redis опционален для планировщика
        logger.warning("RedisJobStore недоступен (%s), джобы — в памяти процесса", exc)

    scheduler = BackgroundScheduler(jobstores=jobstores, timezone=UTC)
    scheduler.add_job(
        _run_job,
        kwargs={"settings": settings},
        trigger="interval",
        hours=settings.satellite_poll_hours,
        id="satellite_catalog",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(tz=UTC),
        replace_existing=True,
    )
    return scheduler


__all__ = ["create_satellite_scheduler"]
