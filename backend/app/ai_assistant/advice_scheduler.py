"""Пересчёт AI-рекомендаций (09-ai §9.4): суточный планировщик + события.

Триггеры обновления advice:
1. Раз в сутки (``AI_ADVICE_DAILY_AT`` «HH:MM» локального времени контейнера,
   default 05:00) — для всех активных полей со сценами текущего сезона.
2. Событийно: новая чистая NDVI-сцена (``scene.ingested``) — поле в очередь.
3. Вручную: POST API-55 / CLI ``advice-refresh`` (cli.py).

Очередь — advice.queue_advice (ThreadPoolExecutor, AI_ADVICE_PARALLELISM);
ошибки отдельных полей не останавливают остальные (§9.5). При провайдере
ollama/выключенном LLM advice не формируется — планировщик не создаётся
(§9 деградация), в тестах (дефолт ollama) — None и не мешает.
"""

import logging
import uuid
from datetime import date

from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.ai_assistant import advice, llm
from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.events import Event, subscribe, unsubscribe
from app.models.agronomy import Field
from app.models.integrations import SatelliteScene
from app.modules.satellite.ingest import SCENE_INGESTED_EVENT

logger = logging.getLogger(__name__)

#: fallback времени суточного прогона при невалидном AI_ADVICE_DAILY_AT
DEFAULT_DAILY_AT = (5, 0)

_JOB_ID = "ai_advice_daily"


def parse_daily_at(value: str) -> tuple[int, int]:
    """«HH:MM» → (hour, minute); при ошибке — warning и fallback 05:00."""
    try:
        hh, mm = value.strip().split(":", 1)
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except ValueError:
        logger.warning("AI_ADVICE_DAILY_AT=%r не валиден — использую 05:00", value)
        return DEFAULT_DAILY_AT


def candidate_field_ids(db: Session, today: date) -> list[uuid.UUID]:
    """Активные поля с чистыми NDVI-сценами текущего сезона (§9.4 п.1)."""
    scene_year = func.extract("year", SatelliteScene.captured_at)
    stmt = (
        select(Field.id)
        .where(
            Field.status == "active",
            exists().where(
                SatelliteScene.field_id == Field.id,
                SatelliteScene.index == "ndvi",
                SatelliteScene.cloud_pct <= advice.CLEAN_CLOUD_PCT,
                scene_year == today.year,
            ),
        )
        .order_by(Field.name)
    )
    return list(db.scalars(stmt))


def on_scene_ingested(event: Event) -> None:
    """Подписчик scene.ingested: новая чистая NDVI-сцена → поле в очередь."""
    payload = event.payload
    if payload.get("index") != "ndvi":
        return
    cloud = payload.get("cloud_pct")
    if cloud is not None and float(cloud) > advice.CLEAN_CLOUD_PCT:
        return
    try:
        field_id = uuid.UUID(str(payload.get("field_id")))
    except (TypeError, ValueError):
        logger.warning("advice: событие scene.ingested без корректного field_id")
        return
    advice.queue_advice(field_id)


def subscribe_advice() -> None:
    """Подключить событийный пересчёт к шине (идемпотентно)."""
    subscribe(SCENE_INGESTED_EVENT, on_scene_ingested)


def unsubscribe_advice() -> None:
    unsubscribe(SCENE_INGESTED_EVENT, on_scene_ingested)


def _run_daily_job() -> None:
    """Суточный прогон: все поля-кандидаты в очередь генерации."""
    from app.core.config import get_settings

    with SessionLocal() as db:
        field_ids = candidate_field_ids(db, date.today())
    if not field_ids:
        logger.info("advice: нет полей со сценами текущего сезона — прогон пуст")
        return
    logger.info("advice: суточный прогон — %d полей в очередь", len(field_ids))
    for field_id in field_ids:
        advice.queue_advice(field_id)


def create_advice_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """Суточный планировщик advice или None при провайдере без advice (§9).

    Ollama/выключенный LLM → планировщик не нужен (записи не создаются).
    Время — локальное время контейнера (§9.4).
    """
    if settings.llm_provider.strip().lower() != "anthropic" or not llm.llm_enabled(settings):
        logger.info("advice: провайдер без advice — суточный планировщик не создаётся")
        return None

    jobstores: dict[str, object] = {}
    try:
        jobstores["default"] = RedisJobStore(
            host=settings.redis_host, port=settings.redis_port, db=settings.redis_db
        )
    except Exception as exc:  # noqa: BLE001 — Redis опционален для планировщика
        logger.warning("RedisJobStore недоступен (%s), джобы — в памяти процесса", exc)

    hour, minute = parse_daily_at(settings.ai_advice_daily_at)
    scheduler = BackgroundScheduler(jobstores=jobstores)
    scheduler.add_job(
        _run_daily_job,
        trigger="cron",
        hour=hour,
        minute=minute,
        id=_JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler


__all__ = [
    "candidate_field_ids",
    "create_advice_scheduler",
    "on_scene_ingested",
    "parse_daily_at",
    "subscribe_advice",
    "unsubscribe_advice",
]
