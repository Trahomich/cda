"""Тесты API-53: прогноз урожайности по NDVI (v3-ai #95, docs/06 §7.4).

Проверяется линейная эмпирика forecast = k_base + k_ndvi_max·ndvi_max +
k_ndvi_int·ndvi_integral (ц/га; ndvi_integral = Σ(ndvi − 0.2)+/100),
интервал ±25 %, ошибки 404 field/cycle, 422 yield_no_season_data /
yield_no_culture (пар) / yield_no_model (коэффициенты NULL), выбор сезона
(цикл года последней сцены, fallback — последний основной цикл как API-01),
RBAC группы satellite. БД — PostgreSQL (DATABASE_URL_TEST, паттерн #20).
"""

import uuid
from datetime import date

import pytest
from app.models.agronomy import CropCycle
from app.models.auth import UserRole
from app.models.integrations import SatelliteScene
from app.models.references import Culture
from app.modules.satellite.router import SATELLITE_SOURCE_ID
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_field, make_satellite_source

API = "/api/v1"


def make_culture(db: Session, *, name="Озимая пшеница", kind="main", coeffs=(-10.0, 40.0, 30.0)):
    culture = Culture(
        name=name,
        kind=kind,
        crop_group="зерновые",
        yield_k_base=coeffs[0],
        yield_k_ndvi_max=coeffs[1],
        yield_k_ndvi_int=coeffs[2],
    )
    db.add(culture)
    db.commit()
    return culture


def make_cycle(db: Session, field_id: uuid.UUID, culture: Culture, year: int, kind="main"):
    cycle = CropCycle(field_id=field_id, year=year, culture_id=culture.id, kind=kind)
    db.add(cycle)
    db.commit()
    return cycle


def make_scene(db: Session, field_id: uuid.UUID, day: date, avg: float):
    db.add(
        SatelliteScene(
            field_id=field_id,
            index="ndvi",
            captured_at=day,
            resolution_m=10,
            index_avg=avg,
            index_min=avg,
            index_max=avg,
            cloud_pct=0.0,
            source_id=SATELLITE_SOURCE_ID,
        )
    )


class TestYieldForecast:
    @pytest.fixture(autouse=True)
    def _setup(self, client, db_session):
        self.client = client
        self.db = db_session
        make_user(db_session, login="yf-agro", role=UserRole.AGRONOMIST, password="pw-yf")
        make_user(
            db_session, login="yf-mach", role=UserRole.MACHINE_OPERATOR, password="pw-yf2"
        )
        self.auth = auth_header(client, "yf-agro", "pw-yf")
        self.field_id = make_field(db_session)
        make_satellite_source(db_session)
        yield

    def _get(self):
        return self.client.get(
            f"{API}/fields/{self.field_id}/yield-forecast", headers=self.auth
        )

    def test_forecast_formula_and_interval(self):
        """Формула §7.4 по подставным коэффициентам и сценам; интервал ±25 %."""
        culture = make_culture(self.db, coeffs=(-10.0, 40.0, 30.0))
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [(date(2026, 5, 1), 0.5), (date(2026, 6, 1), 0.7), (date(2026, 7, 1), 0.8)]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        resp = self._get()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {
            "field_id",
            "cycle_year",
            "culture_id",
            "culture_name",
            "method",
            "ndvi_max",
            "ndvi_integral",
            "forecast_c_ha",
            "low_c_ha",
            "high_c_ha",
            "disclaimer",
        }
        assert body["field_id"] == str(self.field_id)
        assert body["cycle_year"] == 2026
        assert body["culture_id"] == str(culture.id)
        assert body["culture_name"] == "Озимая пшеница"
        assert body["method"] == "ndvi-empirical"
        assert body["disclaimer"]
        # ndvi_max = max средних; integral = Σ(ndvi − 0.2)+/100 = 1.4/100
        assert body["ndvi_max"] == 0.8
        assert body["ndvi_integral"] == pytest.approx(0.014, abs=1e-6)
        # forecast = −10 + 40·0.8 + 30·0.014 = 22.42; ±25 %
        assert body["forecast_c_ha"] == pytest.approx(22.42, abs=0.01)
        assert body["low_c_ha"] == pytest.approx(22.42 * 0.75, abs=0.01)
        assert body["high_c_ha"] == pytest.approx(22.42 * 1.25, abs=0.01)

    def test_integral_skips_values_below_02(self):
        """NDVI ниже 0.2 не даёт вклада в интеграл (Σ (ndvi − 0.2)+)."""
        culture = make_culture(self.db, coeffs=(0.0, 0.0, 30.0))
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [
            (date(2026, 4, 1), 0.1),
            (date(2026, 5, 1), 0.15),
            (date(2026, 6, 1), 0.6),
            (date(2026, 7, 1), 0.555),  # проверка округления ndvi_max до 3 знаков
        ]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        body = self._get().json()
        assert body["ndvi_max"] == 0.6
        # вклад только сцен выше 0.2: (0.6 − 0.2) + (0.555 − 0.2) = 0.755 → 0.00755
        assert body["ndvi_integral"] == pytest.approx(0.00755, abs=1e-6)
        assert body["forecast_c_ha"] == pytest.approx(30 * 0.00755, abs=0.01)

    def test_season_follows_last_scene_year(self):
        """Сезон — год последней сцены: сцены 2026 при циклах 2025 и 2026 → 2026."""
        culture = make_culture(self.db, coeffs=(-10.0, 40.0, 30.0))
        make_cycle(self.db, self.field_id, culture, 2025)
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [(date(2025, 6, 1), 0.4), (date(2025, 7, 1), 0.5), (date(2025, 8, 1), 0.6)]:
            make_scene(self.db, self.field_id, day, avg)
        make_scene(self.db, self.field_id, date(2026, 6, 1), 0.7)
        make_scene(self.db, self.field_id, date(2026, 7, 1), 0.75)
        make_scene(self.db, self.field_id, date(2026, 8, 1), 0.8)
        self.db.commit()

        body = self._get().json()
        assert body["cycle_year"] == 2026
        assert body["ndvi_max"] == 0.8

    def test_fallback_to_latest_cycle_without_scene_year(self):
        """Сцен года цикла нет по году последней сцены → последний основной цикл."""
        culture = make_culture(self.db, coeffs=(-10.0, 40.0, 30.0))
        make_cycle(self.db, self.field_id, culture, 2024)
        for day, avg in [(date(2024, 5, 1), 0.5), (date(2024, 6, 1), 0.7), (date(2024, 7, 1), 0.8)]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        body = self._get().json()
        assert body["cycle_year"] == 2024

    def test_unknown_field_404(self):
        resp = self.client.get(f"{API}/fields/{uuid.uuid4()}/yield-forecast", headers=self.auth)
        assert resp.status_code == 404
        assert resp.json()["code"] == "field_not_found"

    def test_no_cycle_404(self):
        culture = make_culture(self.db)
        for day, avg in [(date(2026, 5, 1), 0.5), (date(2026, 6, 1), 0.7), (date(2026, 7, 1), 0.8)]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        resp = self._get()
        assert resp.status_code == 404
        assert resp.json()["code"] == "cycle_not_found"

    def test_less_than_3_scenes_422(self):
        culture = make_culture(self.db)
        make_cycle(self.db, self.field_id, culture, 2026)
        make_scene(self.db, self.field_id, date(2026, 6, 1), 0.7)
        make_scene(self.db, self.field_id, date(2026, 7, 1), 0.8)
        self.db.commit()

        resp = self._get()
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "yield_no_season_data"

    def test_fallow_cycle_422(self):
        culture = make_culture(self.db, name="Пар", kind="fallow", coeffs=(-10.0, 40.0, 30.0))
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [(date(2026, 5, 1), 0.5), (date(2026, 6, 1), 0.7), (date(2026, 7, 1), 0.8)]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        resp = self._get()
        assert resp.status_code == 422
        assert resp.json()["code"] == "yield_no_culture"

    def test_null_coeffs_422(self):
        """Культура без коэффициентов модели → 422 yield_no_model."""
        culture = make_culture(self.db, name="Гречиха", coeffs=(None, None, None))
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [(date(2026, 5, 1), 0.5), (date(2026, 6, 1), 0.7), (date(2026, 7, 1), 0.8)]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        resp = self._get()
        assert resp.status_code == 422
        assert resp.json()["code"] == "yield_no_model"

    def test_rbac_mechanizer_forbidden(self):
        auth = auth_header(self.client, "yf-mach", "pw-yf2")
        resp = self.client.get(f"{API}/fields/{self.field_id}/yield-forecast", headers=auth)
        assert resp.status_code == 403
