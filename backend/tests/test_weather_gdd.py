"""Тесты API-58 GET /weather/gdd (§7.8, #132): GDD-кривая с 1 января.

gdd_day = max(0, (tmax+tmin)/2 − 5); день без tmin/tmax в сумму не входит
(gdd_day=null); norm_cum — многолетнее среднее gdd_cum по календарной дате,
null при <2 годах истории. БД — DATABASE_URL_TEST; наружу не ходим.
"""

import uuid
from datetime import date

import pytest
from app.models.auth import UserRole
from app.models.integrations import WeatherObservation
from app.modules.weather.rules import GDD_BASE_C
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_weather import API, OpenMeteoAdapter, make_field, make_weather_source

SOURCE = OpenMeteoAdapter.source_id


def add_obs(
    db: Session,
    field_id: uuid.UUID,
    day: date,
    t_min: float | None,
    t_max: float | None,
) -> None:
    db.add(
        WeatherObservation(
            field_id=field_id,
            observed_at=day,
            temperature_min_c=t_min,
            temperature_max_c=t_max,
            source_id=SOURCE,
        )
    )


@pytest.fixture()
def headers(client, db_session) -> dict:
    make_weather_source(db_session)  # FK dat_weather_observation → ref_source
    make_user(db_session, login="gdd-user", role=UserRole.AGRONOMIST, password="secret123")
    return auth_header(client, "gdd-user", "secret123")


class TestGddCurve:
    def test_curve_and_total(self, client, db_session, headers):
        """Серия января: gdd_day, отсечка у нуля, день без температур — null."""
        field_id = make_field(db_session, name="GDD-1")
        add_obs(db_session, field_id, date(2026, 1, 2), 4.0, 10.0)  # +2.0
        add_obs(db_session, field_id, date(2026, 1, 3), 0.0, 8.0)  # −1 → 0
        add_obs(db_session, field_id, date(2026, 1, 4), -6.0, 0.0)  # −5.5 → 0
        add_obs(db_session, field_id, date(2026, 1, 5), None, 9.0)  # нет tmin → null
        add_obs(db_session, field_id, date(2026, 1, 6), 6.0, 12.0)  # +4.0
        db_session.commit()

        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 2026}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["field_id"] == str(field_id)
        assert body["year"] == 2026
        assert body["base_c"] == GDD_BASE_C
        items = body["items"]
        assert [i["date"] for i in items] == [
            "2026-01-02",
            "2026-01-03",
            "2026-01-04",
            "2026-01-05",
            "2026-01-06",
        ]
        assert [i["gdd_day"] for i in items] == [2.0, 0.0, 0.0, None, 4.0]
        assert [i["gdd_cum"] for i in items] == [2.0, 2.0, 2.0, 2.0, 6.0]
        assert body["total"] == 6.0  # последний gdd_cum
        assert body["norm_cum"] is None  # прошлых лет нет

    def test_year_filters_and_default_current(self, client, db_session, headers):
        """Явный year — только его дни; без year — текущий год."""
        field_id = make_field(db_session, name="GDD-2")
        add_obs(db_session, field_id, date(2025, 6, 1), 10.0, 20.0)
        add_obs(db_session, field_id, date(2026, 6, 1), 8.0, 16.0)
        db_session.commit()

        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 2025}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [i["date"] for i in body["items"]] == ["2025-06-01"]
        assert body["total"] == 10.0  # (10+20)/2 − 5

        default = client.get(f"{API}/weather/gdd", params={"field_id": field_id}, headers=headers)
        assert default.status_code == 200
        assert default.json()["year"] == date.today().year


class TestGddNorm:
    def test_norm_null_with_single_past_year(self, client, db_session, headers):
        """Один прошлый год истории — norm_cum=null (норма непредставительна)."""
        field_id = make_field(db_session, name="GDD-3")
        add_obs(db_session, field_id, date(2025, 6, 1), 10.0, 20.0)
        add_obs(db_session, field_id, date(2026, 6, 1), 8.0, 16.0)
        db_session.commit()

        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 2026}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["norm_cum"] is None

    def test_norm_with_two_past_years(self, client, db_session, headers):
        """Два прошлых года: норма = среднее gdd_cum по календарной дате."""
        field_id = make_field(db_session, name="GDD-4")
        # 2024: 01-02 +3.0; 01-03 +1.0 (cum 4.0)
        add_obs(db_session, field_id, date(2024, 1, 2), 6.0, 10.0)  # +3.0
        add_obs(db_session, field_id, date(2024, 1, 3), 5.0, 11.0)  # +3.0 → cum 6.0
        # 2025: 01-02 +4.0
        add_obs(db_session, field_id, date(2025, 1, 2), 7.0, 11.0)  # +4.0
        # 2026: обе даты
        add_obs(db_session, field_id, date(2026, 1, 2), 0.0, 12.0)  # +1.0
        add_obs(db_session, field_id, date(2026, 1, 3), 0.0, 12.0)  # +1.0 → cum 2.0
        db_session.commit()

        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 2026}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # линия нормы — по тем же датам, что items
        assert [p["date"] for p in body["norm_cum"]] == ["2026-01-02", "2026-01-03"]
        # 01-02: (3.0 + 4.0) / 2; 01-03: только 2024 → 6.0
        assert [p["norm_cum"] for p in body["norm_cum"]] == [3.5, 6.0]
        assert body["total"] == 2.0

    def test_norm_missing_date_null(self, client, db_session, headers):
        """Дата запроса отсутствует во всех прошлых годах — норма null."""
        field_id = make_field(db_session, name="GDD-5")
        add_obs(db_session, field_id, date(2024, 1, 2), 6.0, 10.0)
        add_obs(db_session, field_id, date(2025, 1, 2), 7.0, 11.0)
        add_obs(db_session, field_id, date(2026, 3, 8), 8.0, 12.0)  # марта нет в истории
        db_session.commit()

        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 2026}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["norm_cum"] == [{"date": "2026-03-08", "norm_cum": None}]


class TestGddErrors:
    def test_unknown_field_404(self, client, headers):
        resp = client.get(
            f"{API}/weather/gdd",
            params={"field_id": uuid.uuid4(), "year": 2026},
            headers=headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "field_not_found"

    def test_invalid_year_400(self, client, db_session, headers):
        """Невалидный год — ошибка валидации 400 (422 в проекте зарезервирован
        под бизнес-правила, errors.py: RequestValidationError → 400)."""
        field_id = make_field(db_session, name="GDD-err")
        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 1999}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "validation_error"

    def test_no_data_year_empty_items(self, client, db_session, headers):
        """Года без наблюдений — items=[], total=0, norm=null."""
        field_id = make_field(db_session, name="GDD-6")
        add_obs(db_session, field_id, date(2025, 6, 1), 10.0, 20.0)
        db_session.commit()
        resp = client.get(
            f"{API}/weather/gdd", params={"field_id": field_id, "year": 2030}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0.0
        assert body["norm_cum"] is None
