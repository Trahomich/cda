"""Тесты карты погоды хозяйства (API-61 GET /weather/map, §7.8, #135).

Заливка полей по метрике: precip_7d/precip_14d (сумма осадков за окно),
t_now (последняя суточная температура), gdd_ytd (GDD с 1 января, base 5 °C —
как API-58). Контур — упрощённый GeoJSON; кэш 1 ч; неактивные поля и поля
без наблюдений — value=null / вне списка. БД — DATABASE_URL_TEST.
"""

import json
import uuid
from datetime import date, timedelta

import pytest
from app.integrations.open_meteo import OpenMeteoAdapter
from app.models.integrations import WeatherObservation
from sqlalchemy.orm import Session
from tests.test_weather import api_headers, make_field, make_weather_source

API = "/api/v1"


@pytest.fixture(autouse=True)
def _weather_source(db_session):
    """FK dat_weather_observation → ref_source (source_id weather.open-meteo)."""
    make_weather_source(db_session)


def make_obs(
    db: Session,
    field_id: uuid.UUID,
    day: date,
    *,
    t_avg=None,
    t_min=None,
    t_max=None,
    precip=None,
    trust=1.0,
) -> None:
    db.add(
        WeatherObservation(
            field_id=field_id,
            observed_at=day,
            temperature_avg_c=t_avg,
            temperature_min_c=t_min,
            temperature_max_c=t_max,
            precipitation_mm=precip,
            source_id=OpenMeteoAdapter.source_id,
            trust=trust,
        )
    )
    db.commit()


class TestWeatherMapApi:
    def test_precip_7d_sums_window(self, client, db_session):
        """Сумма осадков за 7 суток: вчерашние 5 мм в окне, 10 дней назад — нет."""
        today = date.today()
        f1 = make_field(db_session, name="A")
        f2 = make_field(db_session, name="B")
        make_obs(db_session, f1, today - timedelta(days=1), precip=5.0)
        make_obs(db_session, f1, today - timedelta(days=10), precip=9.0)  # вне окна
        make_obs(db_session, f2, today - timedelta(days=2), precip=2.5)
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", params={"metric": "precip_7d"}, headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["metric"] == "precip_7d" and len(body["items"]) == 2
        by_name = {i["name"]: i for i in body["items"]}
        assert by_name["A"]["value"] == 5.0  # 9 мм 10 дней назад не в окне
        assert by_name["B"]["value"] == 2.5
        assert by_name["A"]["updated_at"] == (today - timedelta(days=1)).isoformat()
        assert by_name["A"]["contour"]["type"] in ("Polygon", "MultiPolygon")

    def test_precip_14d_wider_window(self, client, db_session):
        """14 суток захватывают запись 10 дней назад."""
        today = date.today()
        f1 = make_field(db_session, name="A")
        make_obs(db_session, f1, today - timedelta(days=10), precip=9.0)
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", params={"metric": "precip_14d"}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"][0]["value"] == 9.0

    def test_t_now_latest_day(self, client, db_session):
        """t_now — температура последних суток (t_avg; иначе середина min/max)."""
        today = date.today()
        f1 = make_field(db_session, name="A")
        make_obs(db_session, f1, today - timedelta(days=3), t_avg=10.0)
        make_obs(db_session, f1, today - timedelta(days=1), t_avg=None, t_min=2.0, t_max=8.0)
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", params={"metric": "t_now"}, headers=headers)
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["value"] == 5.0  # (2+8)/2 последних суток, а не 10.0
        assert item["updated_at"] == (today - timedelta(days=1)).isoformat()

    def test_gdd_ytd_matches_api58_base5(self, client, db_session):
        """gdd_ytd — сумма max(0,(tmin+tmax)/2−5) дней года с обеими t."""
        today = date.today()
        f1 = make_field(db_session, name="A")
        make_obs(db_session, f1, today - timedelta(days=2), t_min=2.0, t_max=8.0)  # +0
        make_obs(db_session, f1, today - timedelta(days=1), t_min=6.0, t_max=10.0)  # +3
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", params={"metric": "gdd_ytd"}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"][0]["value"] == 3.0

    def test_dedupe_by_trust(self, client, db_session):
        """Дубль (поле, дата) — берётся запись с большей trust, как API-58."""
        today = date.today()
        f1 = make_field(db_session, name="A")
        make_obs(db_session, f1, today - timedelta(days=1), precip=1.0, trust=1.0)
        make_obs(db_session, f1, today - timedelta(days=1), precip=7.0, trust=0.5)
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", params={"metric": "precip_7d"}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"][0]["value"] == 1.0

    def test_fields_without_observations_null(self, client, db_session):
        """Поле без наблюдений — в списке (контур есть), value/updated_at null."""
        make_field(db_session, name="NoData")
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", headers=headers)  # default precip_7d
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["value"] is None and item["updated_at"] is None
        assert item["contour"]["type"] in ("Polygon", "MultiPolygon")

    def test_inactive_field_excluded(self, client, db_session):
        """Архивные поля на карту не попадают (статусы: active/merged/archived)."""
        make_field(db_session, name="archived", status="archived")
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []

    def test_unknown_metric_422(self, client, db_session):
        headers = api_headers(client, db_session)
        resp = client.get(f"{API}/weather/map", params={"metric": "nope"}, headers=headers)
        assert resp.status_code == 422
        assert resp.json()["code"] == "unknown_metric"

    def test_cache_1h(self, client, db_session, monkeypatch):
        """Повторный запрос — из кэша (значение не пересчитывается)."""
        import app.core.cache as cache_mod

        store: dict[str, str] = {}

        class FakeRedis:
            def get(self, key):
                return store.get(key)

            def setex(self, key, ttl, value):
                store[key] = value

        monkeypatch.setattr(cache_mod, "_redis", FakeRedis())

        today = date.today()
        f1 = make_field(db_session, name="A")
        make_obs(db_session, f1, today - timedelta(days=1), precip=4.0)
        headers = api_headers(client, db_session)

        first = client.get(f"{API}/weather/map", headers=headers)
        assert first.status_code == 200
        assert "weather:map:precip_7d" in store
        # новое наблюдение после кэширования не видно до TTL
        make_obs(db_session, f1, today, precip=100.0)
        second = client.get(f"{API}/weather/map", headers=headers)
        assert second.json()["items"][0]["value"] == 4.0
        assert second.json() == first.json()

    def test_contour_simplified_valid_geojson(self, client, db_session):
        """Контур — валидный GeoJSON с координатами исходного квадрата."""
        make_field(db_session, name="A")
        headers = api_headers(client, db_session)

        resp = client.get(f"{API}/weather/map", params={"metric": "gdd_ytd"}, headers=headers)
        assert resp.status_code == 200, resp.text
        contour = resp.json()["items"][0]["contour"]
        assert contour["type"] in ("Polygon", "MultiPolygon")
        rings = (
            contour["coordinates"]
            if contour["type"] == "MultiPolygon"
            else [contour["coordinates"]]
        )
        xs = [c[0] for ring in rings for c in ring[0]]
        assert 40.0 <= min(xs) and max(xs) <= 40.01
        json.dumps(contour)  # сериализуем
