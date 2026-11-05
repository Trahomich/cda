"""Фоновый опрос погоды (05 §4): текущая — 30 мин, прогноз — 3 ч.

APScheduler BackgroundScheduler; джобы — WeatherIngest.ingest_current /
ingest_forecast с собственными сессиями БД. Включается настройкой
weather_poll_enabled (по умолчанию выключен: тесты и локальная разработка
не должны ходить наружу; в docker-compose api — WEATHER_POLL_ENABLED=true).
Хранилище джоб — Redis (актуально при нескольких воркерах uvicorn); при его
недоступности — in-memory, планирование в рамках процесса сохраняется.
"""

import logging
from datetime import UTC, datetime

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import Settings
from app.core.db import SessionLocal
from app.integrations.open_meteo import OpenMeteoAdapter
from app.modules.weather.ingest import WeatherIngest, centroid_lookup_factory

logger = logging.getLogger(__name__)


def _run_job(kind: str, settings: Settings) -> None:
    """Прогон ingest с собственной сессией БД (вызывается планировщиком)."""
    db = SessionLocal()
    try:
        adapter = OpenMeteoAdapter(
            # центроид вычисляется в момент запроса по сессии прогона
            centroid_lookup=centroid_lookup_factory(db),
            base_url=settings.open_meteo_base_url,
            archive_url=settings.open_meteo_archive_url,
            request_timeout=settings.weather_request_timeout,
        )
        ingest = WeatherIngest(
            adapter,
            retry_attempts=settings.weather_retry_attempts,
            retry_base_seconds=settings.weather_retry_base_seconds,
            forecast_days=settings.weather_forecast_days,
        )
        report = ingest.ingest_current(db) if kind == "current" else ingest.ingest_forecast(db)
        logger.info(
            "weather poll [%s]: ok=%d skipped=%d failed=%d status=%s",
            kind,
            report.ok,
            report.skipped,
            len(report.failed),
            report.status,
        )
    except Exception:  # noqa: BLE001 — фоновая джоба не должна ронять планировщик
        logger.exception("weather poll [%s]: сбой прогона", kind)
    finally:
        db.close()


def create_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """Планировщик опроса погоды или None, если опрос выключен настройками."""
    if not settings.weather_poll_enabled:
        logger.info("опрос погоды выключен (weather_poll_enabled=false)")
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
        kwargs={"kind": "current", "settings": settings},
        trigger="interval",
        minutes=settings.weather_poll_current_minutes,
        id="weather_current",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(tz=UTC),
        replace_existing=True,
    )
    scheduler.add_job(
        _run_job,
        kwargs={"kind": "forecast", "settings": settings},
        trigger="interval",
        hours=settings.weather_poll_forecast_hours,
        id="weather_forecast",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler
