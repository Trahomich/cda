"""Фоновый прогон генераторов алертов (MVP-12).

Основной запуск генераторов — по событиям шины (engine.py: scene.ingested /
forecast.ingested). Планировщик — страховка от пропущенных событий:
периодический полный прогон всех генераторов. Включается настройкой
alerts_poll_enabled (по умолчанию выключен, как у погоды и спутника).
Хранилище джоб — Redis (несколько воркеров uvicorn), при недоступности —
in-memory.
"""

import logging
from datetime import UTC

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _run_job() -> None:
    """Полный прогон генераторов алертов с собственной сессией БД."""
    from app.modules.alerts.engine import AlertEngine

    AlertEngine().run_scheduled()


def create_alerts_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """Планировщик прогона генераторов или None, если алерты выключены настройками."""
    if not settings.alerts_poll_enabled:
        logger.info("периодический прогон алертов выключен (alerts_poll_enabled=false)")
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
        trigger="interval",
        minutes=settings.alerts_poll_minutes,
        id="alerts_check",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler


__all__ = ["create_alerts_scheduler"]
