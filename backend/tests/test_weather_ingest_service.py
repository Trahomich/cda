"""Тесты сервиса weather-ingest (v1-03, ADR-09) и докачки архива фактов.

Проверяется: докачка пропусков ingest_history_gaps (поле без пропусков не
запрашивается, с пропусками — upsert без дублей, DataGap — пропуск),
расписание воркера (джобы/интервалы по 05 §4 и env), run_once-прогон без
сети (httpx.MockTransport, наружу тесты не ходят). БД — DATABASE_URL_TEST.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from app.core.config import Settings
from app.integrations.open_meteo import OpenMeteoAdapter
from app.models.integrations import WeatherObservation
from app.modules.weather.ingest import WeatherIngest, centroid_lookup_factory
from app.services import weather_ingest as service
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.test_weather import (
    SQUARE_GEOJSON,
    load_fixture,
    make_field,
    make_weather_source,
)

API = "/api/v1"


def archive_fixture(day_offsets: list[int]) -> dict:
    """Архивный ответ Open-Meteo со смещениями суток от сегодня (0=сегодня)."""
    return load_fixture("openmeteo_archive.json", day_offsets=day_offsets)


def mock_client(payloads: dict[str, dict | Exception]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        for path, payload in payloads.items():
            if path in str(request.url):
                if isinstance(payload, Exception):
                    return httpx.Response(500, text="source down")
                return httpx.Response(200, json=payload)
        return httpx.Response(404, text=f"unexpected url {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def make_service_ingest(db: Session, payloads: dict[str, dict | Exception]) -> WeatherIngest:
    adapter = OpenMeteoAdapter(
        http=mock_client(payloads),
        centroid_lookup=centroid_lookup_factory(db),
    )
    return WeatherIngest(adapter, retry_attempts=2, retry_base_seconds=0, forecast_days=7)


def insert_observation(db: Session, field_id: uuid.UUID, day: date) -> None:
    db.add(
        WeatherObservation(
            field_id=field_id,
            observed_at=day,
            temperature_avg_c=10.0,
            temperature_min_c=5.0,
            temperature_max_c=15.0,
            precipitation_mm=0.0,
            wind_max_ms=2.0,
            humidity_avg_pct=60.0,
            source_id=OpenMeteoAdapter.source_id,
        )
    )
    db.commit()


def observation_days(db: Session, field_id) -> set[date]:
    return {
        row[0]
        for row in db.execute(
            text(
                "SELECT observed_at FROM dat_weather_observation "
                "WHERE field_id = :fid AND source_id = :src"
            ),
            {"fid": field_id, "src": OpenMeteoAdapter.source_id},
        ).fetchall()
    }


class TestIngestHistoryGaps:
    def test_backfills_missing_days_no_duplicates(self, db_session):
        """Все сутки окна отсутствуют — один запрос архива, upsert всех суток."""
        make_weather_source(db_session)
        field_id = make_field(db_session, name="Долина")
        today = date.today()
        offsets = list(range(-15, 1))  # окно 14 суток до вчера + сегодня
        ingest = make_service_ingest(db_session, {"/archive": archive_fixture(offsets)})

        report = ingest.ingest_history_gaps(db_session, days=14)

        assert report.ok == 1
        days = observation_days(db_session, field_id)
        expected = {date.today() + timedelta(days=off) for off in offsets}
        assert days == expected
        # повторный прогон: пропусков нет — к источнику не обращаемся
        ingest2 = make_service_ingest(db_session, {"/archive": httpx.Response(404)})
        report2 = ingest2.ingest_history_gaps(db_session, days=14)
        assert report2.ok == 0
        assert report2.skipped == 1
        assert observation_days(db_session, field_id) == expected  # ничего не изменилось

    def test_partial_gap_requests_archive_once(self, db_session):
        """Часть суток уже есть — поле всё равно запрашивается, дубли не создаются."""
        make_weather_source(db_session)
        field_id = make_field(db_session, name="Луг")
        today = date.today()
        insert_observation(db_session, field_id, today - timedelta(days=3))
        offsets = list(range(-15, 1))
        ingest = make_service_ingest(db_session, {"/archive": archive_fixture(offsets)})

        report = ingest.ingest_history_gaps(db_session, days=14)

        assert report.ok == 1
        days = observation_days(db_session, field_id)
        assert len(days) == 16
        assert {date.today() + timedelta(days=off) for off in offsets} == days

    def test_source_unavailable_fails_field(self, db_session):
        make_weather_source(db_session)
        make_field(db_session, name="Река")
        ingest = make_service_ingest(db_session, {"/archive": RuntimeError("down")})

        report = ingest.ingest_history_gaps(db_session, days=14)

        assert report.ok == 0
        assert report.failed and "источник недоступен" in report.failed[0]

    def test_inactive_field_ignored(self, db_session):
        make_weather_source(db_session)
        make_field(db_session, name="Архивное", status="archived")
        ingest = make_service_ingest(db_session, {"/archive": archive_fixture([-3])})

        report = ingest.ingest_history_gaps(db_session, days=14)

        assert report.ok == 0 and report.skipped == 0  # полей для запроса нет


class TestWorker:
    def test_worker_jobs_and_intervals(self):
        """Три джобы с интервалами из настроек (05 §4 / v1-03), старт — сразу."""
        settings = Settings(
            ingest_weather_current_minutes=30,
            ingest_weather_forecast_hours=3,
            ingest_weather_archive_minutes=60,
        )
        scheduler = service.create_worker(settings)
        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert set(jobs) == {service.JOB_CURRENT, service.JOB_FORECAST, service.JOB_ARCHIVE}
        assert jobs[service.JOB_CURRENT].trigger.interval == timedelta(minutes=30)
        assert jobs[service.JOB_FORECAST].trigger.interval == timedelta(hours=3)
        assert jobs[service.JOB_ARCHIVE].trigger.interval == timedelta(minutes=60)

    def test_run_once_cycle_no_fields(self, db_session, monkeypatch):
        """run_once по всем трём джобам на пустой БД: не падает, пишет журнал."""
        from app.core.db import SessionLocal

        monkeypatch.setattr(service, "SessionLocal", lambda: db_session)
        settings = Settings(weather_retry_attempts=1, weather_retry_base_seconds=0)

        for kind in ("archive", "current", "forecast"):
            service.run_once(kind, settings)  # не должно упасть

        rows = db_session.execute(
            text("SELECT status FROM dat_source_journal ORDER BY at")
        ).fetchall()
        assert len(rows) == 3

    def test_settings_env_mapping(self, monkeypatch):
        """CONTRACTS §7: INGEST_WEATHER_* / INGEST_RUN_ONCE читаются из env."""
        monkeypatch.setenv("INGEST_WEATHER_CURRENT_MINUTES", "5")
        monkeypatch.setenv("INGEST_WEATHER_FORECAST_HOURS", "1")
        monkeypatch.setenv("INGEST_WEATHER_ARCHIVE_MINUTES", "120")
        monkeypatch.setenv("INGEST_WEATHER_BACKFILL_DAYS", "30")
        monkeypatch.setenv("INGEST_RUN_ONCE", "true")
        s = Settings(_env_file=None)
        assert s.ingest_weather_current_minutes == 5
        assert s.ingest_weather_forecast_hours == 1
        assert s.ingest_weather_archive_minutes == 120
        assert s.ingest_weather_backfill_days == 30
        assert s.ingest_run_once is True
