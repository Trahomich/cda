"""weather-ingest — сервис приёма погоды Open-Meteo (v1-03, ADR-09).

Отдельный контейнер-воркер без внешнего API (ADR-09, docs/02 §8): по
расписанию docs/05 §4 загружает факты и прогнозы по всем активным полям
(координата — центроид контура, ENT-FIELD) и пишет нормализованные записи
в общую БД (dat_weather_observation / dat_forecast) через ingest-слой
модуля weather (app.modules.weather.ingest). Погодные окна RULE-AGRO-09
пересчитываются существующим кодом api по запросу — сервис ничего
дополнительно не вычисляет. Fake-адаптер остаётся для тестов; живой
адаптер — только здесь (в api опрос погоды выключен).

Джобы (env, CONTRACTS.md §7):
- current  — INGEST_WEATHER_CURRENT_MINUTES (default 30, 05 §4):
  суточный факт за сегодня по каждому полю, upsert по UQ;
- forecast — INGEST_WEATHER_FORECAST_HOURS (default 3, 05 §4): прогноз,
  перезапись последней версией (кэш 3 ч для API-29);
- archive  — INGEST_WEATHER_ARCHIVE_MINUTES (default 60, v1-03): докачка
  пропусков архива (archive-api) за окно INGEST_WEATHER_BACKFILL_DAYS
  (default 14 суток) — поле без пропусков не запрашивается.

Общее: INGEST_RUN_ONCE=true — один цикл всех джоб и выход (стенд/тесты).
Журнал прогонов — dat_source_journal + ref_source.last_status (API-28).

Запуск: python -m app.services.weather_ingest
"""

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from app.core.config import Settings
from app.core.db import SessionLocal
from app.integrations.open_meteo import OpenMeteoAdapter
from app.modules.weather.ingest import WeatherIngest, centroid_lookup_factory

logger = logging.getLogger(__name__)

JOB_CURRENT = "weather_current"
JOB_FORECAST = "weather_forecast"
JOB_ARCHIVE = "weather_archive"


def run_once(kind: str, settings: Settings) -> None:
    """Один прогон джобы kind с собственной сессией БД.

    kind: "current" | "forecast" | "archive" (докачка пропусков).
    """
    db = SessionLocal()
    try:
        adapter = OpenMeteoAdapter(
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
        if kind == "current":
            report = ingest.ingest_current(db)
        elif kind == "forecast":
            report = ingest.ingest_forecast(db)
        elif kind == "archive":
            report = ingest.ingest_history_gaps(db, days=settings.ingest_weather_backfill_days)
        else:  # pragma: no cover — защищено вызовами из планировщика
            raise ValueError(f"неизвестная джоба: {kind}")
        logger.info(
            "weather-ingest [%s]: ok=%d skipped=%d failed=%d status=%s",
            kind,
            report.ok,
            report.skipped,
            len(report.failed),
            report.status,
        )
    except Exception:  # noqa: BLE001 — фоновая джоба не должна ронять воркер
        logger.exception("weather-ingest [%s]: сбой прогона", kind)
    finally:
        db.close()


def create_worker(settings: Settings) -> BlockingScheduler:
    """Планировщик воркера: три джобы по расписанию 05 §4 / v1-03.

    Первый прогон каждой джобы — сразу при старте (свежие данные не ждут
    первого интервала).
    """
    scheduler = BlockingScheduler(timezone=UTC)
    now = datetime.now(tz=UTC)
    scheduler.add_job(
        run_once,
        args=("current", settings),
        trigger="interval",
        minutes=settings.ingest_weather_current_minutes,
        id=JOB_CURRENT,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        run_once,
        args=("forecast", settings),
        trigger="interval",
        hours=settings.ingest_weather_forecast_hours,
        id=JOB_FORECAST,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        run_once,
        args=("archive", settings),
        trigger="interval",
        minutes=settings.ingest_weather_archive_minutes,
        id=JOB_ARCHIVE,
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    return scheduler


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "weather-ingest: старт (current=%d мин, forecast=%d ч, archive=%d мин, "
        "backfill=%d сут, run_once=%s)",
        settings.ingest_weather_current_minutes,
        settings.ingest_weather_forecast_hours,
        settings.ingest_weather_archive_minutes,
        settings.ingest_weather_backfill_days,
        settings.ingest_run_once,
    )
    if settings.ingest_run_once:
        for kind in ("archive", "current", "forecast"):
            run_once(kind, settings)
        logger.info("weather-ingest: режим INGEST_RUN_ONCE — цикл выполнен, выход")
        return
    scheduler = create_worker(settings)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("weather-ingest: остановка по сигналу")


if __name__ == "__main__":
    main()
