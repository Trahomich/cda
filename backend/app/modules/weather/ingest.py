"""Ingest-слой модуля погоды (docs/05-integrations.md §4, §6).

Периодичность (05 §4): текущая погода — 30 мин, прогноз — 3 ч (кэш 3 ч).
Каждый прогон: активные поля -> центроид (ST_Centroid контура) -> адаптер ->
нормализованные структуры -> доменные таблицы. Обработка ошибок (05 §6):
- SourceUnavailableError -> статус degraded, ретраи с экспоненциальной
  задержкой (weather_retry_attempts, weather_retry_base_seconds);
- DataQualityError -> запись отбрасывается (лог + причина в журнале);
- DataGapError -> поле пропускается, это не ошибка.

Журнал: строка в dat_source_journal на прогон (ok / degraded / error) +
обновление ref_source.last_status / last_success_at (API-28 читает их).
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import events
from app.integrations import (
    DataGapError,
    DataQualityError,
    ForecastItem,
    Observation,
    SourceUnavailableError,
)
from app.integrations.open_meteo import OpenMeteoAdapter
from app.models.agronomy import Field
from app.models.integrations import Forecast, SourceJournalEntry, WeatherObservation
from app.models.references import Source

logger = logging.getLogger(__name__)

STALE_FORECAST_HOURS = 12
"""Прогноз старше 12 ч помечается stale (docs/05 §5)."""

FORECAST_INGESTED_EVENT = "forecast.ingested"
"""Событие шины: принят свежий прогноз (подписчики — генераторы алертов)."""


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Итог прогона: сколько полей обработано, ошибок и их причины."""

    ok: int
    skipped: int
    failed: list[str]

    @property
    def status(self) -> str:
        """ok — все поля; degraded — часть или полей не было; error — все упали."""
        if self.ok > 0 and not self.failed:
            return "ok"
        if self.ok > 0:
            return "degraded"
        if not self.failed:
            # полей не было или все пропущены (DataGap) — источник не виноват,
            # но и успехом прогон не является
            return "degraded"
        return "error"


def centroid_lookup_factory(db: Session):
    """Центроиды полей по запросу: field_id -> (lon, lat) или None (нет активного поля)."""

    def lookup(field_id: str) -> tuple[float, float] | None:
        row = db.execute(
            text(
                "SELECT ST_X(c::geometry), ST_Y(c::geometry) FROM dat_field, "
                "ST_Centroid(contour) AS c WHERE id = CAST(:fid AS uuid) AND status = 'active'"
            ),
            {"fid": field_id},
        ).fetchone()
        if row is None:
            return None
        return float(row[0]), float(row[1])

    return lookup


class WeatherIngest:
    """Прогон приёма погодных данных по всем активным полям."""

    def __init__(
        self,
        adapter: OpenMeteoAdapter,
        *,
        retry_attempts: int = 5,
        retry_base_seconds: float = 60.0,
        forecast_days: int = 7,
    ) -> None:
        self._adapter = adapter
        self._retry_attempts = max(1, retry_attempts)
        self._retry_base_seconds = retry_base_seconds
        self._forecast_days = forecast_days

    # -- общее --------------------------------------------------------------

    @staticmethod
    def active_fields(db: Session) -> list[tuple[str, str]]:
        """Активные поля: [(field_id, name)] по статусу active."""
        rows = db.execute(
            select(Field.id, Field.name).where(Field.status == "active").order_by(Field.name)
        ).fetchall()
        return [(str(row[0]), row[1]) for row in rows]

    def _fetch_with_retries(self, fetch):
        """Вызвать fetch с экспоненциальными ретраями (только SourceUnavailableError).

        Возвращает (result, error). error != None — все попытки не прошли.
        """
        delay = self._retry_base_seconds
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                return fetch(), None
            except SourceUnavailableError as exc:
                last_error = exc
                logger.warning(
                    "погода: ретрай %d/%d после %s", attempt + 1, self._retry_attempts, exc
                )
                if attempt + 1 < self._retry_attempts and delay > 0:
                    time.sleep(min(delay, 30.0))  # тесты: retry_base_seconds=0 -> без пауз
                    delay *= 2
        return None, f"источник недоступен: {last_error}"

    @staticmethod
    def log_source(db: Session, source_id: str, status: str, reason: str | None) -> None:
        """Строка в dat_source_journal + ref_source.last_status/last_success_at."""
        db.add(
            SourceJournalEntry(
                source_id=source_id,
                at=datetime.now(tz=UTC),
                status=status,
                reason=reason[:500] if reason else None,
            )
        )
        db.execute(
            update(Source)
            .where(Source.source_id == source_id)
            .values(
                last_status=status,
                last_success_at=datetime.now(tz=UTC) if status == "ok" else Source.last_success_at,
            )
        )
        db.commit()

    # -- запись доменных таблиц ----------------------------------------------

    @staticmethod
    def save_observations(db: Session, items: list[Observation]) -> int:
        """Upsert фактов по UQ(field_id, observed_at, source_id) — дедупликация."""
        if not items:
            return 0
        statement = pg_insert(WeatherObservation).values(
            [
                {
                    "field_id": item.field_id,
                    "observed_at": item.observed_at,
                    "temperature_avg_c": item.temperature_avg_c,
                    "temperature_min_c": item.temperature_min_c,
                    "temperature_max_c": item.temperature_max_c,
                    "precipitation_mm": item.precipitation_mm,
                    "wind_max_ms": item.wind_max_ms,
                    "humidity_avg_pct": item.humidity_avg_pct,
                    "cloud_cover_mean_pct": item.cloud_cover_mean_pct,
                    "snowfall_cm": item.snowfall_cm,
                    "snow_depth_cm": item.snow_depth_cm,
                    "precipitation_hours": item.precipitation_hours,
                    "soil_temperature_c": item.soil_temperature_c,
                    "soil_moisture_pct": item.soil_moisture_pct,
                    "et0_mm": item.et0_mm,
                    "uv_index": item.uv_index,
                    "dew_point_c": item.dew_point_c,
                    "pressure_hpa": item.pressure_hpa,
                    "source_id": item.source_id,
                    "trust": item.trust,
                }
                for item in items
            ]
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_dat_weather_observation",
            set_={
                "temperature_avg_c": excluded.temperature_avg_c,
                "temperature_min_c": excluded.temperature_min_c,
                "temperature_max_c": excluded.temperature_max_c,
                "precipitation_mm": excluded.precipitation_mm,
                "wind_max_ms": excluded.wind_max_ms,
                "humidity_avg_pct": excluded.humidity_avg_pct,
                "cloud_cover_mean_pct": excluded.cloud_cover_mean_pct,
                "snowfall_cm": excluded.snowfall_cm,
                "snow_depth_cm": excluded.snow_depth_cm,
                "precipitation_hours": excluded.precipitation_hours,
                "soil_temperature_c": excluded.soil_temperature_c,
                "soil_moisture_pct": excluded.soil_moisture_pct,
                "et0_mm": excluded.et0_mm,
                "uv_index": excluded.uv_index,
                "dew_point_c": excluded.dew_point_c,
                "pressure_hpa": excluded.pressure_hpa,
                "updated_at": datetime.now(tz=UTC),
            },
        )
        db.execute(statement)
        return len(items)

    @staticmethod
    def save_forecast(db: Session, field_id: str, items: list[ForecastItem]) -> int:
        """Перезаписать прогноз поля: последняя версия выигрывает (06 §4)."""
        if not items:
            return 0
        db.execute(delete(Forecast).where(Forecast.field_id == field_id))
        db.add_all(
            [
                Forecast(
                    field_id=field_id,
                    forecast_for=item.forecast_for,
                    issued_at=item.issued_at,
                    temperature_min_c=item.temperature_min_c,
                    temperature_max_c=item.temperature_max_c,
                    precipitation_mm=item.precipitation_mm,
                    wind_max_ms=item.wind_max_ms,
                    humidity_avg_pct=item.humidity_avg_pct,
                    precip_prob_pct=item.precip_prob_pct,
                    soil_temp_c=item.soil_temp_c,
                    soil_moisture_pct=item.soil_moisture_pct,
                    et0_mm=item.et0_mm,
                    uv_index=item.uv_index,
                    dew_point_c=item.dew_point_c,
                    pressure_hpa=item.pressure_hpa,
                    hazard=item.hazard,
                )
                for item in items
            ]
        )
        return len(items)

    # -- прогоны -------------------------------------------------------------

    def ingest_current(self, db: Session) -> IngestReport:
        """Текущая погода по всем активным полям (05 §4: каждые 30 мин)."""
        report = IngestReportBuilder()
        for field_id, name in self.active_fields(db):
            try:
                observation, error = self._fetch_with_retries(
                    lambda fid=field_id: self._adapter.fetch_current(fid)
                )
            except DataGapError as exc:
                report.skip(name, str(exc))
                continue
            except DataQualityError as exc:
                report.fail(name, f"качество данных: {exc}")
                continue
            if error is not None:
                report.fail(name, error)
                continue
            self.save_observations(db, [observation])
            db.commit()
            report.ok()
        report_result = report.build()
        self.log_source(
            db,
            self._adapter.source_id,
            report_result.status,
            "; ".join(report_result.failed) or None,
        )
        return report_result

    def ingest_forecast(self, db: Session) -> IngestReport:
        """Прогноз по всем активным полям (05 §4: каждые 3 ч, кэш 3 ч)."""
        report = IngestReportBuilder()
        for field_id, name in self.active_fields(db):
            try:
                items, error = self._fetch_with_retries(
                    lambda fid=field_id: self._adapter.fetch_forecast(fid, self._forecast_days)
                )
            except DataGapError as exc:
                report.skip(name, str(exc))
                continue
            except DataQualityError as exc:
                # 05 §6: отбрасываем запись и пишем причину в журнал
                report.fail(name, f"качество данных: {exc}")
                continue
            if error is not None:
                report.fail(name, error)
                continue
            self.save_forecast(db, field_id, items)
            db.commit()
            report.ok()
        report_result = report.build()
        self.log_source(
            db,
            self._adapter.source_id,
            report_result.status,
            "; ".join(report_result.failed) or None,
        )
        if report_result.ok:
            # событие после commit: прогноз консистентен в БД (ADR-01)
            events.publish(
                FORECAST_INGESTED_EVENT,
                {"fields": report_result.ok, "source_id": self._adapter.source_id},
            )
        return report_result

    def ingest_history_for(
        self, db: Session, field_id: str, date_from: date, date_to: date
    ) -> IngestReport:
        """Backfill истории по одному полю (административный прогон)."""
        report = IngestReportBuilder()
        try:
            items = self._adapter.fetch_history(field_id, date_from, date_to)
        except SourceUnavailableError as exc:
            report.fail(field_id, f"источник недоступен: {exc}")
        except (DataGapError, DataQualityError) as exc:
            report.skip(field_id, str(exc))
        else:
            self.save_observations(db, items)
            db.commit()
            report.ok()
        report_result = report.build()
        self.log_source(
            db,
            self._adapter.source_id,
            report_result.status,
            "; ".join(report_result.failed) or None,
        )
        return report_result

    def ingest_history_gaps(self, db: Session, *, days: int = 14) -> IngestReport:
        """Докачка пропусков архива фактов по всем активным полям (v1-03).

        Окно — последние ``days`` суток до вчера включительно (сегодня пишет
        джоба current). По каждому полю: какие сутки окна уже есть в
        dat_weather_observation по данному источнику; если пропусков нет —
        поле пропускается без запроса к источнику, иначе один запрос
        fetch_history на всё окно и upsert по UQ (существующие не меняются).
        """
        report = IngestReportBuilder()
        date_to = date.today() - timedelta(days=1)
        date_from = date_to - timedelta(days=days - 1)
        for field_id, name in self.active_fields(db):
            present = {
                row[0]
                for row in db.execute(
                    select(WeatherObservation.observed_at).where(
                        WeatherObservation.field_id == field_id,
                        WeatherObservation.source_id == self._adapter.source_id,
                        WeatherObservation.observed_at >= date_from,
                        WeatherObservation.observed_at <= date_to,
                    )
                ).fetchall()
            }
            expected = {date_from + timedelta(days=i) for i in range(days)}
            missing = sorted(expected - present)
            if not missing:
                report.skip(name, "пропусков в архиве нет")
                continue
            logger.info(
                "погода: докачка архива %s: %d пропущенных суток (с %s по %s)",
                name,
                len(missing),
                missing[0],
                missing[-1],
            )
            try:
                items, error = self._fetch_with_retries(
                    lambda fid=field_id: self._adapter.fetch_history(fid, date_from, date_to)
                )
            except DataGapError as exc:
                # свежие сутки в archive-api появляются с задержкой — это норма
                report.skip(name, str(exc))
                continue
            except DataQualityError as exc:
                report.fail(name, f"качество данных: {exc}")
                continue
            if error is not None:
                report.fail(name, error)
                continue
            saved = self.save_observations(db, items)
            db.commit()
            logger.info("погода: %s — upsert %d фактов архива", name, saved)
            report.ok()
        report_result = report.build()
        self.log_source(
            db,
            self._adapter.source_id,
            report_result.status,
            "; ".join(report_result.failed) or None,
        )
        return report_result

    def backfill_history(self, db: Session, date_from: date, date_to: date) -> IngestReport:
        """Полный бэкфилл истории по всем активным полям (#126, docs/06 §7.5).

        Одно обращение к архиву на поле за весь период (start_date/end_date),
        затем upsert по UQ — идемпотентно, повторный запуск ничего не портит.
        Пропуски за последние сутки закрывает джоба ingest_history_gaps.
        """
        report = IngestReportBuilder()
        for field_id, name in self.active_fields(db):
            try:
                items, error = self._fetch_with_retries(
                    lambda fid=field_id: self._adapter.fetch_history(fid, date_from, date_to)
                )
            except DataGapError as exc:
                report.skip(name, str(exc))
                continue
            except DataQualityError as exc:
                report.fail(name, f"качество данных: {exc}")
                continue
            if error is not None:
                report.fail(name, error)
                continue
            saved = self.save_observations(db, items)
            db.commit()
            logger.info("погода: бэкфилл %s — upsert %d фактов", name, saved)
            report.ok()
        report_result = report.build()
        self.log_source(
            db,
            self._adapter.source_id,
            report_result.status,
            "; ".join(report_result.failed) or None,
        )
        return report_result


class IngestReportBuilder:
    """Накопитель итога прогона (внутренний для WeatherIngest)."""

    def __init__(self) -> None:
        self._ok = 0
        self._skipped = 0
        self._failed: list[str] = []

    def ok(self) -> None:
        self._ok += 1

    def skip(self, name: str, reason: str) -> None:
        self._skipped += 1
        logger.info("погода: поле %s пропущено: %s", name, reason)

    def fail(self, name: str, reason: str) -> None:
        self._failed.append(f"{name}: {reason}")
        logger.warning("погода: поле %s — %s", name, reason)

    def build(self) -> IngestReport:
        return IngestReport(ok=self._ok, skipped=self._skipped, failed=self._failed)


__all__ = ["IngestReport", "STALE_FORECAST_HOURS", "WeatherIngest", "centroid_lookup_factory"]
