"""Фоновый прогон генераторов алертов (MVP-12).

Основной запуск генераторов — по событиям шины (engine.py: scene.ingested /
forecast.ingested). Планировщик — страховка от пропущенных событий:
периодический полный прогон всех генераторов. Включается настройкой
alerts_poll_enabled (по умолчанию выключен, как у погоды и спутника).
"""

import logging

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import Settings

logger = logging.getLogger(__name__)


def create_alerts_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """Планировщик периодического прогона генераторов или None, если выключен."""
    if not settings.alerts_poll_enabled:
        logger.info("периодический прогон алертов выключен (alerts_poll_enabled=false)")
        return None

    def _run() -> None:
        from app.modules.alerts.engine import AlertEngine

        AlertEngine().run_scheduled()

    scheduler = BackgroundScheduler(
        jobstores={"default": RedisJobStore(host=settings.redis_host, port=settings.redis_port)}
        if settings.redis_host
        else None,
        timezone="UTC",
    )
    scheduler.add_job(
        _run,
        "interval",
        minutes=settings.alerts_poll_minutes,
        id="alerts_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


__all__ = ["create_alerts_scheduler"]
