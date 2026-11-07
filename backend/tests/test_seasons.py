"""Тесты исторического NDVI по сезонам (§7.5, эпик #107, задача #110).

API-54 GET /fields/{id}/seasons (годы, счётчики сцен, культура цикла,
is_current, сортировка по убыванию года) и параметр ?year=YYYY у API-06
(фильтр сезона), API-52 (/vra, /vra/map.png: 404 season_not_found без
сцен сезона), API-53 (цикл года YYYY, 422 yield_no_culture /
yield_no_season_data, поле year в ответе) и API-51 (точка привязки кэша —
последняя сцена сезона, детекторы по сценам сезона, 404 season_not_found).
Регресс: без ?year поведение прежнее (текущий сезон / вся история).
"""

import uuid
from datetime import date

import pytest
from app.core import cache as cache_mod
from app.models.auth import UserRole
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_field, make_satellite_source
from tests.test_yield_forecast import make_culture, make_cycle, make_scene

API = "/api/v1"


class TestSeasonsBase:
    """Общие фикстуры: агроном/оператор, поле, источник спутника."""

    @pytest.fixture(autouse=True)
    def _setup(self, client, db_session):
        self.client = client
        self.db = db_session
        make_user(db_session, login="seasons-agro", role=UserRole.AGRONOMIST, password="pw-s")
        make_user(
            db_session, login="seasons-mach", role=UserRole.MACHINE_OPERATOR, password="pw-s2"
        )
        self.auth = auth_header(client, "seasons-agro", "pw-s")
        self.field_id = make_field(db_session)
        make_satellite_source(db_session)
        yield

    def _seasons_2025_2026(self):
        """Поле с сезонами 2025 (3 сцены, цикла нет) и 2026 (3 сцены, гречиха)."""
        culture = make_culture(self.db, name="Гречиха")
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [
            (date(2025, 5, 10), 0.5),
            (date(2025, 6, 10), 0.6),
            (date(2025, 7, 10), 0.7),
            (date(2026, 5, 1), 0.55),
            (date(2026, 6, 1), 0.65),
            (date(2026, 7, 1), 0.75),
        ]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()
        return culture


class TestApi54Seasons(TestSeasonsBase):
    def test_seasons_list(self):
        """Годы по убыванию, счётчики, границы дат, культура цикла, is_current."""
        self._seasons_2025_2026()
        resp = self.client.get(f"{API}/fields/{self.field_id}/seasons", headers=self.auth)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["field_id"] == str(self.field_id)
        assert body["total"] == 2
        assert [it["year"] for it in body["items"]] == [2026, 2025]
        cur, prev = body["items"]
        assert cur["scenes"] == 3
        assert cur["first_date"] == "2026-05-01"
        assert cur["last_date"] == "2026-07-01"
        assert cur["culture_name"] == "Гречиха"
        assert cur["is_current"] is True
        assert prev["scenes"] == 3
        assert prev["first_date"] == "2025-05-10"
        assert prev["last_date"] == "2025-07-10"
        assert prev["culture_name"] is None  # цикла 2025 нет
        assert prev["is_current"] is False

    def test_seasons_empty_field(self):
        """Поле без сцен: пустой список, total 0."""
        resp = self.client.get(f"{API}/fields/{self.field_id}/seasons", headers=self.auth)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_seasons_unknown_field(self):
        resp = self.client.get(f"{API}/fields/{uuid.uuid4()}/seasons", headers=self.auth)
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == "field_not_found"

    def test_seasons_rbac(self):
        """Только группа satellite (07-ux §5): оператор машин — 403."""
        mach = auth_header(self.client, "seasons-mach", "pw-s2")
        resp = self.client.get(f"{API}/fields/{self.field_id}/seasons", headers=mach)
        assert resp.status_code == 403, resp.text


class TestNdviYearFilter(TestSeasonsBase):
    def test_year_filters_season(self):
        """?year=2025 — только сцены 2025; без year — вся история (регресс)."""
        self._seasons_2025_2026()
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi", params={"year": 2025}, headers=self.auth
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert {it["captured_at"][:4] for it in body["items"]} == {"2025"}

        resp_all = self.client.get(f"{API}/fields/{self.field_id}/ndvi", headers=self.auth)
        assert resp_all.json()["total"] == 6

    def test_year_without_season_returns_empty(self):
        """Сезона нет у поля — пустой таймлайн (без 404), как §7.5 для API-06."""
        self._seasons_2025_2026()
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi", params={"year": 2024}, headers=self.auth
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0


class TestVraYear(TestSeasonsBase):
    def test_vra_year_no_season_404(self):
        """Сезона YYYY нет — 404 season_not_found у /vra и /vra/map.png (§7.5)."""
        self._seasons_2025_2026()
        for path in ("vra", "vra/map.png"):
            resp = self.client.get(
                f"{API}/fields/{self.field_id}/{path}", params={"year": 2024}, headers=self.auth
            )
            assert resp.status_code == 404, resp.text
            assert resp.json()["code"] == "season_not_found"

    def test_vra_without_year_regression(self):
        """Без year и без сцен — прежняя 422 vra_no_data (не season_not_found)."""
        resp = self.client.get(f"{API}/fields/{self.field_id}/vra", headers=self.auth)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "vra_no_data"


class TestYieldYear(TestSeasonsBase):
    def test_yield_explicit_year(self):
        """?year=2025 — цикл и сцены 2025; в ответе year=2025."""
        culture = make_culture(self.db, name="Ячмень")
        make_cycle(self.db, self.field_id, culture, 2025)
        make_cycle(self.db, self.field_id, culture, 2026)
        for day, avg in [
            (date(2025, 5, 1), 0.5),
            (date(2025, 6, 1), 0.7),
            (date(2025, 7, 1), 0.8),
            (date(2026, 5, 1), 0.4),
            (date(2026, 6, 1), 0.5),
            (date(2026, 7, 1), 0.6),
        ]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/yield-forecast", params={"year": 2025}, headers=self.auth
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cycle_year"] == 2025
        assert body["year"] == 2025
        assert body["ndvi_max"] == 0.8  # максимумы сезона 2025, а не 2026

    def test_yield_year_no_cycle_422(self):
        """?year без основного цикла — 422 yield_no_culture (§7.5)."""
        self._seasons_2025_2026()
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/yield-forecast", params={"year": 2025}, headers=self.auth
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "yield_no_culture"

    def test_yield_year_few_scenes_422(self):
        """Цикл года есть, чистых сцен < 3 — 422 yield_no_season_data."""
        culture = make_culture(self.db, name="Овёс")
        make_cycle(self.db, self.field_id, culture, 2024)
        make_scene(self.db, self.field_id, date(2024, 6, 1), 0.6)
        make_scene(self.db, self.field_id, date(2024, 7, 1), 0.7)
        self.db.commit()
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/yield-forecast", params={"year": 2024}, headers=self.auth
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "yield_no_season_data"


class TestInsightsYear(TestSeasonsBase):
    @pytest.fixture(autouse=True)
    def _fake_redis(self, monkeypatch):
        """Изолированный кэш: сезоны не должны пересекаться ключами."""
        store: dict[str, str] = {}

        class FakeRedis:
            def get(self, key):
                return store.get(key)

            def setex(self, key, ttl, value):
                store[key] = value

        monkeypatch.setattr(cache_mod, "_redis", FakeRedis())
        self.store = store

    def test_insights_year_no_season_404(self):
        """Сезона YYYY нет — 404 season_not_found (§7.5)."""
        self._seasons_2025_2026()
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/insights", params={"year": 2024}, headers=self.auth
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == "season_not_found"

    def test_insights_year_selects_season(self):
        """?year=2025 — детекторы по сценам сезона 2025 (падение NDVI там),
        ?year=2026 — те же детекторы молчат (сцены сезона растут)."""
        for day, avg in [
            (date(2025, 6, 1), 0.72),
            (date(2025, 6, 10), 0.55),  # −0.17 → ndvi_drop warn
            (date(2026, 6, 1), 0.70),
            (date(2026, 6, 10), 0.75),  # рост — без выводов
        ]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()

        resp25 = self.client.get(
            f"{API}/fields/{self.field_id}/insights", params={"year": 2025}, headers=self.auth
        )
        assert resp25.status_code == 200, resp25.text
        codes25 = [it["code"] for it in resp25.json()["items"]]
        assert "ndvi_drop" in codes25

        resp26 = self.client.get(
            f"{API}/fields/{self.field_id}/insights", params={"year": 2026}, headers=self.auth
        )
        assert resp26.status_code == 200, resp26.text
        assert "ndvi_drop" not in [it["code"] for it in resp26.json()["items"]]

    def test_insights_seasons_cached_separately(self):
        """Кэш-ключ — последняя дата сезона: сцены сезонов не смешиваются."""
        for day, avg in [
            (date(2025, 6, 1), 0.72),
            (date(2025, 6, 10), 0.55),
            (date(2026, 6, 1), 0.70),
            (date(2026, 6, 10), 0.75),
        ]:
            make_scene(self.db, self.field_id, day, avg)
        self.db.commit()
        for year in (2025, 2026):
            resp = self.client.get(
                f"{API}/fields/{self.field_id}/insights", params={"year": year}, headers=self.auth
            )
            assert resp.status_code == 200, resp.text
        keys = sorted(k for k in self.store if k.startswith("insights:"))
        assert keys == [
            f"insights:{self.field_id}:2025-06-10",
            f"insights:{self.field_id}:2026-06-10",
        ]
