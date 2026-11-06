"""Связка генераторов алертов с шиной событий (ADR-01) и их периодический запуск.

Генераторы алертов слушают события приёма данных:
- scene.ingested (модуль спутника, MVP-10) → проверка падения NDVI по полю
  и спутниковой паузы;
- forecast.ingested (модуль погоды, MVP-6) → погодная проверка.

Обработчики открывают собственную сессию БД (события публикуются после
commit модуля-автора); ошибка обработчика логируется шиной и не мешает
автору события.
"""

import logging
import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.core import events
from app.modules.alerts.generators import AlertGenerators
from app.modules.satellite.ingest import SCENE_INGESTED_EVENT
from app.modules.weather.ingest import FORECAST_INGESTED_EVENT

logger = logging.getLogger(__name__)


class AlertEngine:
    """Подписчик шины: события приёма данных → генераторы алертов."""

    def __init__(
        self,
        generators: AlertGenerators | None = None,
        *,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._generators = generators or AlertGenerators()
        if session_factory is None:
            from app.core.db import SessionLocal

            session_factory = SessionLocal
        self._session_factory = session_factory

    # -- обработчики событий ---------------------------------------------------

    def on_scene_ingested(self, event: events.Event) -> None:
        """scene.ingested: проверить падение NDVI по полю и спутниковую паузу."""
        db = self._session_factory()
        try:
            field_id = event.payload.get("field_id")
            report = self._generators.check_ndvi(db, _as_uuid(field_id))
            gap = self._generators.check_satellite_gap(db)
            logger.info(
                "alerts по %s: ndvi +%d (дублей %d), satellite_gap +%d (дублей %d)",
                field_id,
                report.created,
                report.duplicates,
                gap.created,
                gap.duplicates,
            )
        except Exception:  # noqa: BLE001 — обработчик шины не должен ронять автора
            logger.exception("alerts: сбой обработки %s", SCENE_INGESTED_EVENT)
        finally:
            db.close()

    def on_forecast_ingested(self, event: events.Event) -> None:
        """forecast.ingested: погодная проверка по всем полям."""
        db = self._session_factory()
        try:
            report = self._generators.check_weather(db)
            logger.info(
                "alerts по %s: weather +%d (дублей %d)",
                FORECAST_INGESTED_EVENT,
                report.created,
                report.duplicates,
            )
        except Exception:  # noqa: BLE001
            logger.exception("alerts: сбой обработки %s", FORECAST_INGESTED_EVENT)
        finally:
            db.close()

    # -- подписка/периодический прогон ------------------------------------------

    def subscribe(self) -> None:
        """Подписать обработчики на события приёма данных (идемпотентно)."""
        events.subscribe(SCENE_INGESTED_EVENT, self.on_scene_ingested)
        events.subscribe(FORECAST_INGESTED_EVENT, self.on_forecast_ingested)

    def unsubscribe(self) -> None:
        """Снять подписки (остановка фонового сервиса)."""
        events.unsubscribe(SCENE_INGESTED_EVENT, self.on_scene_ingested)
        events.unsubscribe(FORECAST_INGESTED_EVENT, self.on_forecast_ingested)

    def run_scheduled(self) -> None:
        """Периодический прогон всех генераторов (вызывается планировщиком)."""
        db = self._session_factory()
        try:
            reports = self._generators.check_all(db)
            logger.info(
                "alerts прогон: weather +%d, ndvi +%d, satellite_gap +%d",
                reports["weather"].created,
                reports["ndvi"].created,
                reports["satellite_gap"].created,
            )
        except Exception:  # noqa: BLE001
            logger.exception("alerts: сбой периодического прогона")
        finally:
            db.close()


def _as_uuid(value: str | None) -> uuid.UUID | None:
    """field_id из payload события (str) → UUID; некорректное → None."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


__all__ = ["AlertEngine"]
