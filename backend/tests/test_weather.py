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
    """Реальная структура ответа Open-Meteo; даты при необходимости — от сегодня.

    day_offsets сдвигает daily И hourly на одну дельту (почва #193 должна
    совпадать по датам с daily).
    """
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if day_offsets is not None:
        base = date.fromisoformat(payload["daily"]["time"][0])
        new_first = date.today() + timedelta(days=day_offsets[0])
        payload["daily"]["time"] = [
            (date.today() + timedelta(days=off)).isoformat() for off in day_offsets
        ]
        delta = new_first - base
        hourly_times = (payload.get("hourly") or {}).get("time") or []
        if hourly_times:
            payload["hourly"]["time"] = [
                (date.fromisoformat(t[:10]) + delta).isoformat() + t[10:] for t in hourly_times
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

    def test_29_and_07_precip_prob_pct_from_provider(self, client, db_session):
        """precip_prob_pct доезжает из провайдера в API-29 и API-07 (§7.2),
        включая null-случай (провайдер не отдал вероятность)."""
        field_id = make_field(db_session)
        make_weather_source(db_session)
        ingest = make_ingest(
            db_session,
            {"/forecast": load_fixture("openmeteo_forecast.json", day_offsets=list(range(7)))},
        )
        assert ingest.ingest_forecast(db_session).ok == 1
        headers = api_headers(client, db_session)
        forecast = client.get(
            f"{API}/weather/forecast",
            params={"field_id": field_id, "days": 7},
            headers=headers,
        )
        assert forecast.status_code == 200, forecast.text
        expected = [10, 35, 90, 55, None, 0, 45]
        assert [i["precip_prob_pct"] for i in forecast.json()["items"]] == expected
        weather = client.get(f"{API}/fields/{field_id}/weather", headers=headers)
        assert weather.status_code == 200, weather.text
        assert [i["precip_prob_pct"] for i in weather.json()["forecast"]] == expected

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


# -- история погоды: облачность/снег + бэкфилл (#126) -----------------------


class TestWeatherCloudSnow:
    """Новые метрики фактов (cloud/snow/hours) и полный бэкфилл истории."""

    def test_history_normalizes_cloud_snow_hours(self, db_session):
        """Архив: облачность/снег/часы осадков попадают в Observation (#126)."""
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/archive": load_fixture("openmeteo_archive.json")})
        observations = adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))
        assert len(observations) == 3
        assert observations[0].cloud_cover_mean_pct == pytest.approx(65.0)
        assert observations[2].cloud_cover_mean_pct == pytest.approx(80.0)
        assert observations[2].snowfall_cm == pytest.approx(1.5)
        assert observations[2].snow_depth_cm == pytest.approx(4.0)
        assert observations[2].precipitation_hours == pytest.approx(6.5)
        assert observations[0].snowfall_cm == pytest.approx(0.0)

    def test_forecast_daily_includes_cloud_snow_params(self, db_session):
        """Прогноз запрашивает общие метрики + probability (forecast-only)."""
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
        assert "cloud_cover_mean" in seen["url"]
        assert "snowfall_sum" in seen["url"]
        assert "precipitation_probability_max" in seen["url"]

    def test_history_request_omits_forecast_only_metrics(self, db_session):
        """Архив не отдаёт вероятность осадков — и не запрашивает её (#126)."""
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=load_fixture("openmeteo_archive.json"))

        adapter = OpenMeteoAdapter(
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            centroid_lookup=lambda _: (40.5, 51.5),
        )
        adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))
        assert "cloud_cover_mean" in seen["url"]
        assert "precipitation_hours" in seen["url"]
        assert "precipitation_probability_max" not in seen["url"]

    @pytest.mark.parametrize(
        ("metric", "value", "match"),
        [
            ("cloud_cover_mean", 150.0, "облачность"),
            ("snowfall_sum", -1.0, "снегопад"),
            ("snow_depth_max", -0.5, "высота снега"),
            ("precipitation_hours", 30.0, "часы осадков"),
        ],
    )
    def test_data_quality_new_metrics(self, db_session, metric, value, match):
        """Диапазоны 05 §5 для новых метрик (05 §5 -> DataQualityError)."""
        payload = load_fixture("openmeteo_archive.json")
        payload["daily"][metric] = [value] * 3
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/archive": payload})
        with pytest.raises(DataQualityError, match=match):
            adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))

    def test_backfill_history_upsert_idempotent(self, db_session):
        """Бэкфилл: один запрос на поле, upsert по UQ — повтор не плодит строк."""
        make_field(db_session, name="Бэкфилл-1")
        make_weather_source(db_session)
        ingest = make_ingest(db_session, {"/archive": load_fixture("openmeteo_archive.json")})

        report = ingest.backfill_history(db_session, date(2026, 5, 8), date(2026, 5, 10))
        assert report.ok == 1 and not report.failed
        count = db_session.scalar(text("SELECT count(*) FROM dat_weather_observation"))
        assert count == 3
        row = db_session.execute(
            text(
                "SELECT cloud_cover_mean_pct, snowfall_cm, precipitation_hours "
                "FROM dat_weather_observation WHERE observed_at = '2026-05-10'"
            )
        ).first()
        assert row is not None
        assert float(row[0]) == pytest.approx(80.0)
        assert float(row[1]) == pytest.approx(1.5)
        assert float(row[2]) == pytest.approx(6.5)

        # повторный прогон того же окна — upsert, число строк не меняется
        report2 = ingest.backfill_history(db_session, date(2026, 5, 8), date(2026, 5, 10))
        assert report2.ok == 1
        assert db_session.scalar(text("SELECT count(*) FROM dat_weather_observation")) == 3

    def test_history_endpoint_filters_and_new_fields(self, client, db_session):
        """GET /weather/history: фильтр периода, новые метрики в ответе."""
        field_id = make_field(db_session)
        make_weather_source(db_session, last_status="ok")
        db_session.add_all(
            [
                WeatherObservation(
                    field_id=field_id,
                    observed_at=day,
                    temperature_avg_c=5.0,
                    precipitation_mm=0.0,
                    cloud_cover_mean_pct=cloud,
                    snowfall_cm=snow,
                    snow_depth_cm=snow,
                    precipitation_hours=hours,
                    source_id=OpenMeteoAdapter.source_id,
                )
                for day, cloud, snow, hours in [
                    (date(2026, 1, 1), 90.0, 2.0, 4.0),
                    (date(2026, 1, 2), 70.0, 0.0, 1.0),
                    (date(2026, 1, 3), 10.0, 0.0, 0.0),
                ]
            ]
        )
        db_session.commit()
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/history",
            params={
                "field_id": field_id,
                "date_from": "2026-01-02",
                "date_to": "2026-01-02",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1 and len(body["items"]) == 1
        item = body["items"][0]
        assert item["observed_at"] == "2026-01-02"
        assert item["cloud_cover_mean_pct"] == 70.0
        assert item["snowfall_cm"] == 0.0
        assert item["precipitation_hours"] == 1.0
        assert body["meta"]["source_id"] == OpenMeteoAdapter.source_id

        # полный период + пагинация
        full = client.get(
            f"{API}/weather/history",
            params={"field_id": field_id, "date_from": "2026-01-01", "date_to": "2026-01-03"},
            headers=headers,
        )
        assert full.json()["total"] == 3
        limited = client.get(
            f"{API}/weather/history",
            params={
                "field_id": field_id,
                "date_from": "2026-01-01",
                "date_to": "2026-01-03",
                "limit": 2,
            },
            headers=headers,
        )
        assert len(limited.json()["items"]) == 2 and limited.json()["total"] == 3

    def test_history_endpoint_422_invalid_period(self, client, db_session):
        field_id = make_field(db_session)
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/history",
            params={"field_id": field_id, "date_from": "2026-02-01", "date_to": "2026-01-01"},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_period"

    def test_history_endpoint_404_unknown_field(self, client, db_session):
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/history",
            params={
                "field_id": uuid.uuid4(),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
            headers=headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "field_not_found"


class TestSoilEt0Weather193:
    """#193: почва (hourly→суточные средние, влажность м³/м³→%), ET0, УФ,
    точка росы, давление — в факте, прогнозе и API."""

    def test_history_normalizes_new_metrics(self, db_session):
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/archive": load_fixture("openmeteo_archive.json")})
        obs = adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))
        first = obs[0]
        assert first.soil_temperature_c == pytest.approx(
            15.0 + sum(i * 0.1 for i in range(24)) / 24, abs=0.01
        )
        # влажность: (0.22 + средний прирост) × 100
        assert 22.0 < first.soil_moisture_pct < 25.0
        assert first.et0_mm == pytest.approx(2.1)
        assert first.uv_index == pytest.approx(4.2)
        assert first.dew_point_c == pytest.approx(7.3)
        assert first.pressure_hpa == pytest.approx(1013.6)

    def test_forecast_new_metrics(self, db_session):
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/forecast": load_fixture("openmeteo_forecast.json")})
        items = adapter.fetch_forecast(str(field_id), days=7)
        first = items[0]
        assert first.soil_temp_c is not None and 14.0 < first.soil_temp_c < 18.0
        assert 22.0 < first.soil_moisture_pct < 25.0
        assert first.et0_mm == pytest.approx(2.1) and first.uv_index == pytest.approx(4.2)
        assert first.dew_point_c == pytest.approx(7.3)
        assert first.pressure_hpa == pytest.approx(1013.6)

    @pytest.mark.parametrize(
        ("metric", "value", "match"),
        [
            ("et0_fao_evapotranspiration", -0.5, "ET0"),
            ("uv_index_max", 25.0, "УФ"),
            ("pressure_msl_mean", 700.0, "давление"),
            ("dew_point_2m_mean", 99.0, "точка росы"),
        ],
    )
    def test_data_quality_new_metrics(self, db_session, metric, value, match):
        payload = load_fixture("openmeteo_archive.json")
        payload["daily"][metric] = [value] * 3
        payload["hourly"] = {}  # почву не проверяем
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/archive": payload})
        with pytest.raises(DataQualityError, match=match):
            adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))

    def test_soil_quality_bounds(self, db_session):
        """Почва вне диапазона (T/влажность) — DataQualityError."""
        payload = load_fixture("openmeteo_archive.json")
        key = "soil_temperature_0_to_7cm"
        payload["hourly"][key] = [60.0] * 24  # > 50 °C
        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = adapter_for(db_session, {"/archive": payload})
        with pytest.raises(DataQualityError, match="почвы"):
            adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))

    def test_api_history_and_forecast_return_new_fields(self, client, db_session):

        field_id = make_field(db_session)
        make_weather_source(db_session, last_status="ok")
        db_session.add(
            WeatherObservation(
                field_id=field_id,
                observed_at=date.today() - timedelta(days=1),
                precipitation_mm=1.0,
                soil_temperature_c=17.2,
                soil_moisture_pct=23.4,
                et0_mm=2.8,
                uv_index=5.1,
                dew_point_c=8.9,
                pressure_hpa=1012.3,
                source_id=OpenMeteoAdapter.source_id,
            )
        )
        db_session.commit()
        headers = api_headers(client, db_session)
        resp = client.get(f"{API}/fields/{field_id}/weather", headers=headers)
        assert resp.status_code == 200
        fact = resp.json()["facts"][0]
        assert fact["soil_temperature_c"] == pytest.approx(17.2)
        assert fact["soil_moisture_pct"] == pytest.approx(23.4)
        assert fact["et0_mm"] == pytest.approx(2.8)
        assert fact["uv_index"] == pytest.approx(5.1)
        assert fact["dew_point_c"] == pytest.approx(8.9)
        assert fact["pressure_hpa"] == pytest.approx(1012.3)
        hist = client.get(
            f"{API}/weather/history",
            params={
                "field_id": field_id,
                "date_from": (date.today() - timedelta(days=2)).isoformat(),
                "date_to": date.today().isoformat(),
            },
            headers=headers,
        )
        assert hist.json()["items"][0]["soil_temperature_c"] == pytest.approx(17.2)

    def test_forecast_stored_with_new_fields(self, client, db_session):
        from tests.test_weather import make_ingest

        make_field(db_session)
        make_weather_source(db_session)
        ingest = make_ingest(
            db_session,
            {"/forecast": load_fixture("openmeteo_forecast.json", day_offsets=list(range(7)))},
        )
        ingest.ingest_forecast(db_session)
        from app.models.integrations import Forecast as ForecastModel
        from sqlalchemy import select

        row = db_session.scalars(select(ForecastModel)).first()
        assert row is not None
        assert row.soil_temp_c is not None and float(row.et0_mm) == pytest.approx(2.1)
        assert float(row.uv_index) == pytest.approx(4.2)
        assert float(row.pressure_hpa) == pytest.approx(1013.6)


class TestRateLimit429:
    """#193: 429 (дневной лимит IP) — недоступность источника, не DataGap."""

    def test_429_is_source_unavailable(self, db_session):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": True, "reason": "Daily API request limit exceeded"},
                headers={"Retry-After": "71600"},
            )

        make_field(db_session)
        field_id = db_session.scalar(text("SELECT id FROM dat_field"))
        adapter = OpenMeteoAdapter(
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            centroid_lookup=centroid_lookup_factory(db_session),
        )
        with pytest.raises(SourceUnavailableError, match="429"):
            adapter.fetch_history(str(field_id), date(2026, 5, 8), date(2026, 5, 10))


class TestHistoryBatch196:
    """#196: батч координат архив-API (25 полей/запрос) и 429 без ретраев."""

    @staticmethod
    def _batch_payload(coords: int, days: int = 3) -> dict:
        """Ответ архив-API на батч: метрики — списки списков по локациям."""
        base_day = date(2026, 5, 8)
        times = [(base_day + timedelta(days=i)).isoformat() for i in range(days)]
        return {
            "daily": {
                "time": times,
                "temperature_2m_max": [
                    [16.0 + i + loc for i in range(days)] for loc in range(coords)
                ],
                "temperature_2m_min": [[5.0 + loc for _ in range(days)] for loc in range(coords)],
                "precipitation_sum": [[1.0] * days for _ in range(coords)],
                "wind_speed_10m_max": [[3.0] * days for _ in range(coords)],
                "relative_humidity_2m_mean": [[60] * days for _ in range(coords)],
                "cloud_cover_mean": [[40] * days for _ in range(coords)],
                "snowfall_sum": [[0.0] * days for _ in range(coords)],
                "snow_depth_max": [[0.0] * days for _ in range(coords)],
                "precipitation_hours": [[1.0] * days for _ in range(coords)],
                "et0_fao_evapotranspiration": [[2.0] * days for _ in range(coords)],
                "uv_index_max": [[4.0] * days for _ in range(coords)],
                "dew_point_2m_mean": [[6.0] * days for _ in range(coords)],
                "pressure_msl_mean": [[1013.0] * days for _ in range(coords)],
            },
            "hourly": {
                "time": [f"{times[0]}T{h:02d}:00" for h in range(24)],
                "soil_temperature_0_to_7cm": [[15.0 + loc] * 24 for loc in range(coords)],
                "soil_moisture_1_to_3cm": [[0.2 + loc * 0.01] * 24 for loc in range(coords)],
            },
        }

    def test_batch_request_and_split(self, db_session):
        """Один запрос на N координат; ответ раскладывается по полям."""
        make_field(db_session, name="Батч-1")
        make_field(db_session, name="Батч-2")
        field_ids = [
            str(r[0])
            for r in db_session.execute(text("SELECT id FROM dat_field ORDER BY name")).all()
        ]
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=self._batch_payload(2))

        adapter = OpenMeteoAdapter(
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            centroid_lookup=lambda fid: (40.5, 51.5),
        )
        result = adapter.fetch_history_batch(
            [(fid, 40.5, 51.5) for fid in field_ids], date(2026, 5, 8), date(2026, 5, 10)
        )
        # один запрос с двумя координатами
        assert "51.5%2C51.5" in seen["url"] and "40.5%2C40.5" in seen["url"]
        assert set(result) == set(field_ids)
        first, second = (result[fid] for fid in field_ids)
        assert len(first) == 3 and len(second) == 3
        # локация различима: t_max первой 16.0, второй 17.0
        assert first[0].temperature_max_c == pytest.approx(16.0)
        assert second[0].temperature_max_c == pytest.approx(17.0)
        assert first[0].soil_temperature_c == pytest.approx(15.0)
        assert second[0].soil_temperature_c == pytest.approx(16.0)
        assert first[0].et0_mm == pytest.approx(2.0)

    def test_backfill_batches_fields(self, db_session):
        """backfill_history: 2 поля → ОДИН запрос архив-API (батч)."""
        make_field(db_session, name="Батч-3")
        make_field(db_session, name="Батч-4")
        make_weather_source(db_session)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=self._batch_payload(2))

        adapter = OpenMeteoAdapter(
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            centroid_lookup=lambda fid: (40.5, 51.5),
        )
        ingest = WeatherIngest(adapter, retry_attempts=2, retry_base_seconds=0)
        report = ingest.backfill_history(db_session, date(2026, 5, 8), date(2026, 5, 10))
        assert calls["n"] == 1  # один батч на все поля ≤ WEATHER_BACKFILL_BATCH
        assert report.ok == 2 and not report.failed
        count = db_session.scalar(text("SELECT count(*) FROM dat_weather_observation"))
        assert count == 6  # 2 поля × 3 суток

    def test_429_stops_without_retries(self, db_session):
        """429 — стоп прогона, поля не ретраятся, лимит не жжётся."""
        for i in range(3):
            make_field(db_session, name=f"Батч-лимит-{i}")
        make_weather_source(db_session)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(
                429,
                json={"error": True, "reason": "Daily API request limit exceeded"},
                headers={"Retry-After": "71600"},
            )

        adapter = OpenMeteoAdapter(
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            centroid_lookup=lambda fid: (40.5, 51.5),
        )
        ingest = WeatherIngest(adapter, retry_attempts=5, retry_base_seconds=0)
        report = ingest.backfill_history(db_session, date(2026, 5, 8), date(2026, 5, 10))
        assert calls["n"] == 1  # ни одного повторного запроса
        assert report.ok == 0 and report.failed  # первый батч failed, стоп


class TestHistoryNormMm200:
    """#200: API-57 отдаёт norm_mm — многолетнюю норму осадков по дате."""

    @staticmethod
    def _obs(db, field_id, day, mm):
        db.add(
            WeatherObservation(
                field_id=field_id,
                observed_at=day,
                precipitation_mm=mm,
                source_id=OpenMeteoAdapter.source_id,
            )
        )

    def test_norm_with_two_past_years(self, client, db_session):
        from tests.test_weather import api_headers, make_field

        field_id = make_field(db_session, name="Норма-1")
        make_weather_source(db_session)
        # два прошлых полных года на 5 мая: 2024 — 2.0, 2025 — 6.0 → норма 4.0
        self._obs(db_session, field_id, date(2024, 5, 5), 2.0)
        self._obs(db_session, field_id, date(2025, 5, 5), 6.0)
        self._obs(db_session, field_id, date(2026, 5, 5), 9.0)  # текущий год
        db_session.commit()
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/history",
            params={"field_id": field_id, "date_from": "2026-05-01", "date_to": "2026-05-10"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        items = {i["observed_at"]: i for i in resp.json()["items"]}
        assert items["2026-05-05"]["norm_mm"] == pytest.approx(4.0)
        # дата без истории в прошлых годах — null
        assert items["2026-05-05"].get("precipitation_mm") == pytest.approx(9.0)

    def test_norm_null_with_one_past_year(self, client, db_session):
        from tests.test_weather import api_headers, make_field

        field_id = make_field(db_session, name="Норма-2")
        make_weather_source(db_session)
        self._obs(db_session, field_id, date(2025, 5, 5), 6.0)
        self._obs(db_session, field_id, date(2026, 5, 5), 9.0)
        db_session.commit()
        headers = api_headers(client, db_session)
        resp = client.get(
            f"{API}/weather/history",
            params={"field_id": field_id, "date_from": "2026-05-01", "date_to": "2026-05-10"},
            headers=headers,
        )
        assert resp.json()["items"][0]["norm_mm"] is None


class TestPrecipWindowUnified204:
    """#204: «осадки за 7 суток» — одно число (окно завершённых суток
    T−7..T−1) в карте API-61, сводке API-07 и детекторах вымокания."""

    def test_map_and_summary_same_number(self, client, db_session):
        from app.modules.weather.router import WEATHER_MAP_CACHE_TTL  # noqa: F401

        field_id = make_field(db_session, name="Окно-7")
        make_weather_source(db_session)
        today = date.today()
        # 8 суток осадков: T-8..T-1 по 1.5 мм, сегодня (T) — 9.9 мм,
        # которые НЕ должны попасть в 7-суточное окно
        for back in range(1, 9):
            db_session.add(
                WeatherObservation(
                    field_id=field_id,
                    observed_at=today - timedelta(days=back),
                    precipitation_mm=1.5,
                    source_id=OpenMeteoAdapter.source_id,
                )
            )
        db_session.add(
            WeatherObservation(
                field_id=field_id,
                observed_at=today,
                precipitation_mm=9.9,
                source_id=OpenMeteoAdapter.source_id,
            )
        )
        db_session.commit()
        headers = api_headers(client, db_session)

        summary = client.get(f"{API}/fields/{field_id}/weather", headers=headers).json()
        assert summary["precip_7d"] == pytest.approx(7 * 1.5)  # 10.5, без 9.9
        assert summary["precip_14d"] == pytest.approx(8 * 1.5)  # 12.0

        import app.core.cache as cache_mod
        from tests.test_vra import FakeRedis

        fake = FakeRedis()
        orig = cache_mod._redis
        cache_mod._redis = fake
        try:
            from app.modules.weather.router import _weather_map_cache_key

            cache_mod.client().flushall() if hasattr(cache_mod.client(), "flushall") else None

            cache_key = _weather_map_cache_key("precip_7d")
            # сбрасываем возможный кэш карты
            fake.store.pop(cache_key, None)
            m = client.get(f"{API}/weather/map", params={"metric": "precip_7d"}, headers=headers)
            assert m.status_code == 200, m.text
            item = next(i for i in m.json()["items"] if i["field_id"] == str(field_id))
            assert item["value"] == pytest.approx(7 * 1.5)  # то же число, что в сводке
        finally:
            cache_mod._redis = orig

    def test_waterlog_window_excludes_scene_day(self, db_session):
        """Детектор вымокания: осадки ДНЯ СЦЕНЫ не входят в окно 7 суток."""
        from app.modules.satellite.waterlog import precip_sums_7d

        field_id = make_field(db_session)
        make_weather_source(db_session)
        scene_day = date(2026, 6, 10)
        for back in range(1, 8):  # 03..09.06
            db_session.add(
                WeatherObservation(
                    field_id=field_id,
                    observed_at=scene_day - timedelta(days=back),
                    precipitation_mm=4.0,
                    source_id=OpenMeteoAdapter.source_id,
                )
            )
        db_session.add(
            WeatherObservation(
                field_id=field_id,
                observed_at=scene_day,  # сам день сцены — вне окна
                precipitation_mm=50.0,
                source_id=OpenMeteoAdapter.source_id,
            )
        )
        db_session.commit()
        assert precip_sums_7d(db_session, field_id, [scene_day])[scene_day] == pytest.approx(28.0)
