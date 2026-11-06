"""Тесты модуля погоды (задача MVP-6, API-07/29, RULE-AGRO-09).

Проверяется: нормализация ответов Open-Meteo (реальная структура — фикстуры
tests/fixtures/openmeteo_*.json), валидация 05 §5 (DataQualityError), перевод
сетевых ошибок в SourceUnavailableError, ingest-слой (дедуп UQ фактов,
перезапись прогноза, журнал dat_source_journal, статусы ref_source),
погодные окна по ref_weather_rule (параметры влияют на вердикт), API-07/29
и RBAC группы weather. Внешние HTTP — httpx.MockTransport, наружу тесты
не ходят. БД — PostgreSQL+PostGIS (DATABASE_URL_TEST, паттерн #20).
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from app.integrations import (
    DataGapError,
    DataQualityError,
    SourceUnavailableError,
)
from app.integrations.open_meteo import OpenMeteoAdapter
from app.models.auth import UserRole
from app.models.integrations import Forecast, WeatherObservation
from app.models.references import Source
from app.modules.weather.ingest import WeatherIngest, centroid_lookup_factory
from app.modules.weather.rules import (
    DEFAULT_WEATHER_RULES,
    DayMetrics,
    evaluate_day,
    seed_weather_rules,
)
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user

FIXTURES = Path(__file__).parent / "fixtures"
API = "/api/v1"

SQUARE_GEOJSON = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [
            [
                [40.0, 51.0],
                [40.01, 51.0],
                [40.01, 51.01],
                [40.0, 51.01],
                [40.0, 51.0],
            ]
        ],
    }
)


def make_field(db: Session, name="Степное-1", status="active") -> uuid.UUID:
    """Поле напрямую в БД: ingest-слой не зависит от API модуля fields (WIP #23)."""
    field_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, :status)"
        ),
        {"id": field_id, "name": name, "geo": SQUARE_GEOJSON, "status": status},
    )
    db.commit()
    return field_id


def make_weather_source(db: Session, *, last_status=None) -> None:
    db.add(Source(source_id=OpenMeteoAdapter.source_id, kind="weather", last_status=last_status))
    db.commit()


def load_fixture(name: str, *, day_offsets: list[int] | None = None) -> dict:
    """Реальная структура ответа Open-Meteo; даты при необходимости — от сегодня."""
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if day_offsets is not None:
        payload["daily"]["time"] = [
            (date.today() + timedelta(days=off)).isoformat() for off in day_offsets
        ]
    return payload


def mock_client(payloads: dict[str, dict | Exception]) -> httpx.Client:
    """httpx.Client с перехватом: путь URL -> ответ (dict) или исключение."""

    def handler(request: httpx.Request) -> httpx.Response:
        for path, payload in payloads.items():
            if path in str(request.url):
                if isinstance(payload, Exception):
                    return httpx.Response(500, text="source down")
                return httpx.Response(200, json=payload)
        return httpx.Response(404, text=f"unexpected url {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def adapter_for(db: Session, payloads: dict[str, dict | Exception]) -> OpenMeteoAdapter:
    return OpenMeteoAdapter(
        http=mock_client(payloads),
        centroid_lookup=centroid_lookup_factory(db),
    )


def make_ingest(db: Session, payloads: dict[str, dict | Exception]) -> WeatherIngest:
    return WeatherIngest(
        adapter_for(db, payloads),
        retry_attempts=2,
        retry_base_seconds=0,  # без пауз между ретраями
        forecast_days=7,
    )


# -- адаптер ---------------------------------------------------------------


class TestOpenMeteoAdapter:
    def test_forecast_normalization_and_hazard(self, db_session):
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/forecast": load_fixture("openmeteo_forecast.json")})
        items = adapter.fetch_forecast(str(field_id), days=7)
        assert len(items) == 7
        assert items[0].source_id == "weather.open-meteo"
        assert items[0].temperature_max_c == pytest.approx(18.4)
        assert items[0].temperature_min_c == pytest.approx(7.2)
        # hazard (05 §3): 13.05 — осадки 16.8 мм -> heavy_rain; 15.05 — t_min -1.3 -> frost
        by_day = {i.forecast_for: i for i in items}
        heavy = [d for d, i in by_day.items() if i.precipitation_mm == 16.8]
        frost = [d for d, i in by_day.items() if i.temperature_min_c == -1.3]
        assert by_day[heavy[0]].hazard == "heavy_rain"
        assert by_day[frost[0]].hazard == "frost"
        assert by_day[min(by_day)].hazard == "none"

    def test_forecast_requests_windspeed_in_ms(self, db_session):
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=load_fixture("openmeteo_forecast.json"))

        adapter = OpenMeteoAdapter(
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            centroid_lookup=lambda _: (40.5, 51.5),
        )
        adapter.fetch_forecast(str(field_id), days=7)
        assert "windspeed_unit=ms" in seen["url"]
        assert "timezone=auto" in seen["url"]

    def test_history_t_avg_is_midrange(self, db_session):
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/archive": load_fixture("openmeteo_archive.json")})
        observations = adapter.fetch_history(
            str(field_id), date.today() - timedelta(days=3), date.today() - timedelta(days=1)
        )
        assert len(observations) == 3
        assert observations[0].temperature_avg_c == pytest.approx((16.2 + 5.4) / 2)
        assert observations[1].precipitation_mm == pytest.approx(3.5)

    def test_data_quality_temperature_out_of_range(self, db_session):
        payload = load_fixture("openmeteo_current.json")
        payload["daily"]["temperature_2m_max"] = [70.0]  # > 60 °C (05 §5)
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/forecast": payload})
        with pytest.raises(DataQualityError, match="60"):
            adapter.fetch_current(str(field_id))

    def test_data_quality_wind_over_60_ms(self, db_session):
        payload = load_fixture("openmeteo_current.json")
        payload["daily"]["wind_speed_10m_max"] = [75.0]
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/forecast": payload})
        with pytest.raises(DataQualityError, match="ветер"):
            adapter.fetch_current(str(field_id))

    def test_forecast_precip_prob_pct_int_and_null(self, db_session):
        """Вероятность осадков нормализуется в int; null сохраняется (§7.2)."""
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(
            db_session,
            {"/forecast": load_fixture("openmeteo_forecast.json", day_offsets=list(range(7)))},
        )
        items = adapter.fetch_forecast(str(field_id), days=7)
        # фикстура: вероятности [10, 35, 90, 55, None, 0, 45]
        values = [i.precip_prob_pct for i in items]
        assert values == [10, 35, 90, 55, None, 0, 45]
        assert all(v is None or isinstance(v, int) for v in values)

    def test_data_quality_precip_prob_out_of_range(self, db_session):
        payload = load_fixture("openmeteo_current.json")
        payload["daily"]["precipitation_probability_max"] = [120]  # > 100 % (05 §5)
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/forecast": payload})
        with pytest.raises(DataQualityError, match="вероятность"):
            adapter.fetch_current(str(field_id))

    def test_source_unavailable_on_500(self, db_session):
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/forecast": SourceUnavailableError("x")})
        with pytest.raises(SourceUnavailableError):
            adapter.fetch_forecast(str(field_id), days=7)

    def test_no_centroid_is_data_gap(self, db_session):
        adapter = OpenMeteoAdapter(http=mock_client({}), centroid_lookup=lambda _: None)
        with pytest.raises(DataGapError):
            adapter.fetch_forecast(str(uuid.uuid4()), days=7)


# -- ingest ----------------------------------------------------------------


class TestIngest:
    def test_forecast_writes_and_rewrites(self, db_session):
        make_field(db_session)
        make_weather_source(db_session)
        str(db_session.scalar(text("SELECT id FROM dat_field")))
        ingest = make_ingest(
            db_session,
            {"/forecast": load_fixture("openmeteo_forecast.json", day_offsets=list(range(7)))},
        )
        report = ingest.ingest_forecast(db_session)
        assert (report.ok, report.failed) == (1, [])
        assert db_session.query(Forecast).count() == 7
        # вторая версия прогноза перезаписывает, а не дублирует (06 §4)
        ingest.ingest_forecast(db_session)
        assert db_session.query(Forecast).count() == 7

    def test_current_observation_dedup_by_uq(self, db_session):
        make_field(db_session)
        make_weather_source(db_session)
        ingest = make_ingest(
            db_session,
            {"/forecast": load_fixture("openmeteo_current.json", day_offsets=[0])},
        )
        report = ingest.ingest_current(db_session)
        assert report.ok == 1
        assert db_session.query(WeatherObservation).count() == 1
        ingest.ingest_current(db_session)  # тот же день — дедуп UQ
        assert db_session.query(WeatherObservation).count() == 1

    def test_journal_and_source_status_on_error(self, db_session):
        make_field(db_session)
        make_weather_source(db_session)
        ingest = make_ingest(db_session, {"/forecast": SourceUnavailableError("down")})
        report = ingest.ingest_forecast(db_session)
        assert report.status == "error"
        assert "недоступен" in report.failed[0]
        from app.models.integrations import SourceJournalEntry

        entry = db_session.query(SourceJournalEntry).one()
        assert entry.status == "error"
        assert entry.reason and "недоступен" in entry.reason
        source = db_session.query(Source).one()
        assert source.last_status == "error"

    def test_journal_ok_on_success(self, db_session):
        make_field(db_session)
        make_weather_source(db_session)
        ingest = make_ingest(
            db_session,
            {"/forecast": load_fixture("openmeteo_forecast.json", day_offsets=list(range(7)))},
        )
        report = ingest.ingest_forecast(db_session)
        assert report.status == "ok"
        source = db_session.query(Source).one()
        assert source.last_status == "ok"
        assert source.last_success_at is not None

    def test_data_quality_rejected_with_journal(self, db_session):
        payload = load_fixture("openmeteo_forecast.json", day_offsets=list(range(7)))
        payload["daily"]["temperature_2m_min"][0] = -70.0
        make_field(db_session)
        make_weather_source(db_session)
        ingest = make_ingest(db_session, {"/forecast": payload})
        report = ingest.ingest_forecast(db_session)
        assert report.status == "error" and "качество" in report.failed[0]
        assert db_session.query(Forecast).count() == 0  # запись отброшена (05 §6)

    def test_inactive_field_ignored(self, db_session):
        make_field(db_session, name="Старое", status="archived")
        make_weather_source(db_session)
        ingest = make_ingest(db_session, {"/forecast": SourceUnavailableError("x")})
        report = ingest.ingest_forecast(db_session)
        # активных полей нет -> ни запросов, ни ошибок; статус — degraded (не success)
        assert report.ok == 0 and report.failed == []
        assert report.status == "degraded"


# -- правила RULE-AGRO-09 ---------------------------------------------------


class TestRules:
    def test_seed_weather_rules_idempotent(self, db_session):
        assert seed_weather_rules(db_session) == 4
        seed_weather_rules(db_session)  # upsert, не дубль
        count = db_session.execute(text("SELECT count(*) FROM ref_weather_rule")).scalar_one()
        assert count == 4

    def test_spraying_good_day_ok(self):
        rule = DEFAULT_WEATHER_RULES[0]["params"]
        verdict = evaluate_day(
            rule,
            DayMetrics(
                day=date.today(),
                temperature_max_c=18.4,
                temperature_min_c=7.2,
                precipitation_mm=0.0,
                wind_max_ms=3.4,
                humidity_avg_pct=62,
            ),
            prev_precip=0.0,
        )
        assert verdict.ok and verdict.reasons == []

    def test_spraying_wind_blocks_with_reason(self):
        rule = DEFAULT_WEATHER_RULES[0]["params"]
        verdict = evaluate_day(
            rule,
            DayMetrics(
                day=date.today(),
                temperature_max_c=18.4,
                temperature_min_c=7.2,
                precipitation_mm=0.0,
                wind_max_ms=5.6,
                humidity_avg_pct=62,
            ),
            prev_precip=0.0,
        )
        assert not verdict.ok
        assert any("ветер" in r for r in verdict.reasons)

    def test_seeding_requires_prev_day_precip(self):
        rule = DEFAULT_WEATHER_RULES[2]["params"]  # seeding
        metrics = DayMetrics(
            day=date.today(),
            temperature_max_c=10.0,
            temperature_min_c=2.0,  # Tср = 6 >= 4
            precipitation_mm=None,
            wind_max_ms=None,
            humidity_avg_pct=None,
        )
        assert evaluate_day(rule, metrics, prev_precip=12.0).ok is False
        assert evaluate_day(rule, metrics, prev_precip=3.0).ok is True

    def test_tunable_params_change_verdict(self):
        # manager может ослабить порог ветра в ref_weather_rule — окно откроется
        strict = dict(DEFAULT_WEATHER_RULES[0]["params"])  # wind_max 4
        relaxed = {**strict, "wind_max": 6.0}
        metrics = DayMetrics(
            day=date.today(),
            temperature_max_c=18.4,
            temperature_min_c=7.2,
            precipitation_mm=0.0,
            wind_max_ms=5.6,
            humidity_avg_pct=62,
        )
        assert evaluate_day(strict, metrics, 0.0).ok is False
        assert evaluate_day(relaxed, metrics, 0.0).ok is True


# -- API-07 / API-29 / окна -------------------------------------------------


def api_forecast_rows(db: Session, field_id, rows: list[tuple[int, float, float, float]]) -> None:
    """Прогноз напрямую: rows = [(offset_days, t_max, t_min, precip)]."""
    db.add_all(
        [
            Forecast(
                field_id=field_id,
                forecast_for=date.today() + timedelta(days=off),
                issued_at=datetime.now(tz=UTC),
                temperature_max_c=t_max,
                temperature_min_c=t_min,
                precipitation_mm=precip,
                wind_max_ms=3.0,
                humidity_avg_pct=60,
                hazard="none",
            )
            for off, t_max, t_min, precip in rows
        ]
    )
    db.commit()


def api_headers(client, db_session, role=UserRole.AGRONOMIST) -> dict:
    make_user(db_session, login=f"u_{role.value}", role=role)
    return auth_header(client, f"u_{role.value}", "secret123")


class TestWeatherApi:
    def test_07_returns_facts_forecast_and_meta(self, client, db_session):
        field_id = make_field(db_session)
        make_weather_source(db_session, last_status="ok")
        db_session.add(
            WeatherObservation(
                field_id=field_id,
                observed_at=date.today() - timedelta(days=1),
                temperature_avg_c=12.0,
                temperature_min_c=6.0,
                temperature_max_c=18.0,
                precipitation_mm=0.0,
                wind_max_ms=3.0,
                humidity_avg_pct=60,
                source_id=OpenMeteoAdapter.source_id,
            )
        )
        api_forecast_rows(db_session, field_id, [(0, 18.4, 7.2, 0.0), (1, 21.9, 10.1, 0.2)])
        headers = api_headers(client, db_session)
        resp = client.get(f"{API}/fields/{field_id}/weather", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["facts"]) == 1 and body["facts"][0]["temperature_avg_c"] == 12.0
        assert len(body["forecast"]) == 2
        assert body["meta"]["status"] == "ok" and body["meta"]["stale"] is False

    def test_07_marks_stale_forecast(self, client, db_session):
        field_id = make_field(db_session)
        make_weather_source(db_session)
        db_session.add(
            Forecast(
                field_id=field_id,
                forecast_for=date.today() + timedelta(days=1),
                issued_at=datetime.now(tz=UTC) - timedelta(hours=13),  # > 12 ч (05 §5)
                temperature_max_c=20,
                temperature_min_c=8,
                precipitation_mm=0,
                wind_max_ms=3,
                humidity_avg_pct=60,
            )
        )
        db_session.commit()
        headers = api_headers(client, db_session)
        resp = client.get(f"{API}/fields/{field_id}/weather", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["meta"]["stale"] is True

    def test_07_404_unknown_field(self, client, db_session):
        headers = api_headers(client, db_session)
        resp = client.get(f"{API}/fields/{uuid.uuid4()}/weather", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "field_not_found"

    def test_29_forecast_days_limiter(self, client, db_session):
        field_id = make_field(db_session)
        make_weather_source(db_session)
        api_forecast_rows(
            db_session,
            field_id,
            [(d, 20, 8, 0.0) for d in range(5)],
        )
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/forecast",
            params={"field_id": field_id, "days": 3},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["items"]) == 3
        assert body["total"] == 5 and body["limit"] == 3 and body["offset"] == 0

    def test_windows_ok_and_blocked_days(self, client, db_session):
        field_id = make_field(db_session)
        seed_weather_rules(db_session)
        # день 0: окно spraying открыто; день 1: ветер блокирует (в прогнозе 9 м/с)
        db_session.add_all(
            [
                Forecast(
                    field_id=field_id,
                    forecast_for=date.today(),
                    issued_at=datetime.now(tz=UTC),
                    temperature_max_c=18.4,
                    temperature_min_c=7.2,
                    precipitation_mm=0.0,
                    wind_max_ms=3.4,
                    humidity_avg_pct=62,
                ),
                Forecast(
                    field_id=field_id,
                    forecast_for=date.today() + timedelta(days=1),
                    issued_at=datetime.now(tz=UTC),
                    temperature_max_c=21.9,
                    temperature_min_c=10.1,
                    precipitation_mm=0.2,
                    wind_max_ms=9.0,
                    humidity_avg_pct=55,
                ),
            ]
        )
        db_session.commit()
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/windows",
            params={"operation_type": "spraying", "field_id": field_id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["window_open"] is True
        assert body["alert"] is None
        assert body["days"][0]["ok"] is True
        assert body["days"][1]["ok"] is False
        assert any("ветер" in r for r in body["days"][1]["reasons"])

    def test_windows_no_window_alert_text(self, client, db_session):
        field_id = make_field(db_session)
        seed_weather_rules(db_session)
        api_forecast_rows(db_session, field_id, [(0, 5.0, -5.0, 20.0), (1, 4.0, -6.0, 25.0)])
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/windows",
            params={"operation_type": "spraying", "field_id": field_id},
            headers=headers,
        )
        body = resp.json()
        assert body["window_open"] is False
        assert body["alert"] and "окна" in body["alert"]

    def test_windows_unknown_operation_422(self, client, db_session):
        field_id = make_field(db_session)
        seed_weather_rules(db_session)
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/windows",
            params={"operation_type": "bathing", "field_id": field_id},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "unknown_operation_type"

    def test_rbac_weather_group_allows_machine_operator(self, client, db_session):
        field_id = make_field(db_session)
        headers = api_headers(client, db_session, role=UserRole.MACHINE_OPERATOR)
        resp = client.get(
            f"{API}/weather/forecast",
            params={"field_id": field_id, "days": 3},
            headers=headers,
        )
        assert resp.status_code == 200

    def test_rbac_401_without_token(self, client, db_session):
        field_id = make_field(db_session)
        resp = client.get(f"{API}/weather/forecast", params={"field_id": field_id, "days": 3})
        assert resp.status_code == 401
