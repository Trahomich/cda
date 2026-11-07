"""Тесты «Выводов ассистента» (AI-01b, docs/06 §7.4, 09-ai §8.2, задача #93).

GET /fields/{id}/insights: числовые детекторы на синтетических сценах/погоде/
агрохимии, кэш 24 ч по (поле, дата сцены), деградация без Ollama
(source="rules", summary=null), 404 на чужое поле, фоновой LLM-генерации в
тестах нет (OLLAMA_BASE_URL пуст — background не планируется). Пороги
waterlogging/vra — точки истины waterlog.py/vra.py.
"""

import uuid
from datetime import date, timedelta

import pytest
from app.models.integrations import SatelliteScene, WeatherObservation
from app.models.references import Agrochem, Source
from tests.test_inputs import headers, make_culture, make_cycle, make_field

API = "/api/v1"
SATELLITE_SOURCE = "satellite.sentinel2-l2a"
WEATHER_SOURCE = "weather.open-meteo"
TODAY = date(2026, 10, 31)


@pytest.fixture()
def world(client, db_session):
    """Агроном, источники; ответ эндпоинта для поля без данных."""
    hdr = headers(client, db_session)
    db_session.add(Source(source_id=SATELLITE_SOURCE, kind="satellite"))
    db_session.add(Source(source_id=WEATHER_SOURCE, kind="weather"))
    db_session.commit()
    return client, hdr


def make_scene(
    db,
    field_id: uuid.UUID,
    captured_at: date,
    index_avg: float,
    *,
    ndwi_mean: float | None = None,
    ndwi_p90: float | None = None,
) -> SatelliteScene:
    scene = SatelliteScene(
        field_id=field_id,
        index="ndvi",
        captured_at=captured_at,
        resolution_m=10,
        index_avg=index_avg,
        index_min=index_avg,
        index_max=index_avg,
        cloud_pct=0.0,
        ndwi_mean=ndwi_mean,
        ndwi_p90=ndwi_p90,
        source_id=SATELLITE_SOURCE,
    )
    db.add(scene)
    db.commit()
    return scene


def make_obs(
    db,
    field_id: uuid.UUID,
    observed_at: date,
    *,
    precip_mm: float | None = 0.0,
    t_min: float | None = None,
    t_max: float | None = None,
) -> None:
    db.add(
        WeatherObservation(
            field_id=field_id,
            observed_at=observed_at,
            precipitation_mm=precip_mm,
            temperature_min_c=t_min,
            temperature_max_c=t_max,
            source_id=WEATHER_SOURCE,
        )
    )


def get_insights(client, hdr, field_id) -> dict:
    resp = client.get(f"{API}/fields/{field_id}/insights", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


def codes(items: list[dict]) -> list[str]:
    return [item["code"] for item in items]


def item(body: dict, code: str) -> dict:
    found = [it for it in body["items"] if it["code"] == code]
    assert len(found) == 1, f"{code} не найден: {body['items']}"
    return found[0]


class TestDetectorsApi:
    def test_empty_field_no_items_static_summary(self, world) -> None:
        """Поле без данных: items=[], summary «отклонений не выявлено»."""
        client, hdr = world
        field = make_field(client, hdr)
        body = get_insights(client, hdr, uuid.UUID(field["field_id"]))
        assert body["items"] == []
        assert body["source"] == "rules"
        assert body["summary"] == "Отклонений по данным детекторов не выявлено."
        assert body["field_id"] == field["field_id"]
        assert body["generated_at"]

    def test_ndvi_drop_warn_and_alert(self, world, db_session) -> None:
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY - timedelta(days=1), 0.55)  # −0.17 → warn
        body = get_insights(client, hdr, fid)
        assert item(body, "ndvi_drop")["severity"] == "warn"
        assert "−0.17" in item(body, "ndvi_drop")["detail"]

        fid2 = uuid.UUID(make_field(client, hdr, name="Поле-алерт")["field_id"])
        make_scene(db_session, fid2, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid2, TODAY - timedelta(days=1), 0.40)  # −0.32 → alert
        body2 = get_insights(client, hdr, fid2)
        assert item(body2, "ndvi_drop")["severity"] == "alert"

    def test_ndvi_drop_old_scene_ignored(self, world, db_session) -> None:
        """Падение старше окна 30 суток от последней сцены — не повод для вывода."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=40), 0.80)
        make_scene(db_session, fid, TODAY - timedelta(days=35), 0.45)  # −0.35, но старое
        make_scene(db_session, fid, TODAY, 0.46)
        body = get_insights(client, hdr, fid)
        assert "ndvi_drop" not in codes(body["items"])

    def test_ndvi_stagnation(self, world, db_session) -> None:
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        for i, ndvi in enumerate((0.45, 0.46, 0.47)):  # прирост 0.02 < 0.05, среднее < 0.7
            make_scene(db_session, fid, TODAY - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert item(body, "ndvi_stagnation")["severity"] == "info"

    def test_ndvi_stagnation_silent_when_peak(self, world, db_session) -> None:
        """Средний NDVI ≥ 0.7 (пик вегетации) — стагнация нормальна."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        for i, ndvi in enumerate((0.70, 0.71, 0.71)):
            make_scene(db_session, fid, TODAY - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert "ndvi_stagnation" not in codes(body["items"])

    def test_ndvi_stagnation_silent_out_of_season(self, world, db_session) -> None:
        """Ноябрь, поля без цикла — фолбэк «обычное» окно 4–10: вывода нет (§8.2 v1.1)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        for i, ndvi in enumerate((0.45, 0.46, 0.47)):
            make_scene(db_session, fid, date(2026, 11, 20) - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert "ndvi_stagnation" not in codes(body["items"])

    def test_ndvi_stagnation_in_season(self, world, db_session) -> None:
        """Тот же ряд в мае (сезон, среднее ≥ 0.3) — детектор работает."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        for i, ndvi in enumerate((0.45, 0.46, 0.47)):
            make_scene(db_session, fid, date(2026, 5, 20) - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert item(body, "ndvi_stagnation")["severity"] == "info"

    def test_ndvi_stagnation_silent_low_vegetation(self, world, db_session) -> None:
        """Май (сезон), но средний NDVI < 0.3 — вегетации нет, стагнация не о чём."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        for i, ndvi in enumerate((0.18, 0.19, 0.19)):  # среднее 0.187 < 0.3
            make_scene(db_session, fid, date(2026, 5, 20) - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert "ndvi_stagnation" not in codes(body["items"])

    def test_ndvi_stagnation_winter_culture_november(self, world, db_session) -> None:
        """Озимая пшеница (цикл 2027, осенний сев) — ноябрь в её окне: item."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        culture = make_culture(db_session, name="Пшеница озимая")
        make_cycle(db_session, field={"field_id": str(fid)}, culture=culture, year=2027)
        for i, ndvi in enumerate((0.45, 0.46, 0.47)):
            make_scene(db_session, fid, date(2026, 11, 20) - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert item(body, "ndvi_stagnation")["severity"] == "info"

    def test_ndvi_stagnation_spring_culture_november_silent(self, world, db_session) -> None:
        """Яровая пшеница в ноябре — вне окна 4–10: вывода нет."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        culture = make_culture(db_session, name="Пшеница яровая")
        make_cycle(db_session, field={"field_id": str(fid)}, culture=culture, year=2027)
        for i, ndvi in enumerate((0.45, 0.46, 0.47)):
            make_scene(db_session, fid, date(2026, 11, 20) - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert "ndvi_stagnation" not in codes(body["items"])

    def test_ndvi_stagnation_winter_culture_july_silent(self, world, db_session) -> None:
        """Июль вне окна даже озимых (март–июнь, сентябрь–ноябрь): нет вывода."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        culture = make_culture(db_session, name="Рожь озимая")
        make_cycle(db_session, field={"field_id": str(fid)}, culture=culture, year=2027)
        for i, ndvi in enumerate((0.45, 0.46, 0.47)):
            make_scene(db_session, fid, date(2027, 7, 20) - timedelta(days=10 - 4 * i), ndvi)
        body = get_insights(client, hdr, fid)
        assert "ndvi_stagnation" not in codes(body["items"])

    def test_waterlogging_warn_and_alert(self, world, db_session) -> None:
        """warn — эпизод не на последней сцене (доля пикселей мала); alert —
        эпизод на последней сцене и/или ndwi_mean ≥ порога p90 (§8.2 v3)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        episode_day = TODAY - timedelta(days=5)
        make_scene(db_session, fid, episode_day, 0.40, ndwi_mean=0.02, ndwi_p90=0.12)
        make_scene(db_session, fid, TODAY, 0.42, ndwi_mean=0.02, ndwi_p90=0.05)  # чистая
        for back in range(7):  # 25 мм за 7 суток к дате эпизода
            make_obs(db_session, fid, episode_day - timedelta(days=back), precip_mm=25 / 7)
        db_session.commit()
        body = get_insights(client, hdr, fid)
        assert item(body, "waterlogging")["severity"] == "warn"

        fid2 = uuid.UUID(make_field(client, hdr, name="Мокрое")["field_id"])
        make_scene(db_session, fid2, TODAY, 0.40, ndwi_mean=0.13, ndwi_p90=0.18)
        for back in range(7):
            make_obs(db_session, fid2, TODAY - timedelta(days=back), precip_mm=30 / 7)
        db_session.commit()
        body2 = get_insights(client, hdr, fid2)
        assert item(body2, "waterlogging")["severity"] == "alert"

    def test_waterlogging_silent_without_rain(self, world, db_session) -> None:
        """NDWI высокий, но осадков < 20 мм — условия §7.4 не выполнены."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY, 0.40, ndwi_mean=0.12, ndwi_p90=0.15)
        for back in range(7):
            make_obs(db_session, fid, TODAY - timedelta(days=back), precip_mm=1.0)
        db_session.commit()
        body = get_insights(client, hdr, fid)
        assert "waterlogging" not in codes(body["items"])

    def test_waterlogging_mid_season_episode_warn(self, world, db_session) -> None:
        """Эпизод в середине сезона (дожди только там): warn с датой эпизода,
        хотя последняя сцена чистая (§8.2 v3 — анализ всех сцен сезона)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr, name="Средне-мокрое")["field_id"])
        dates = [TODAY - timedelta(days=4 * back) for back in range(5)]  # 5 сцен
        for day in dates:
            make_scene(db_session, fid, day, 0.40, ndwi_mean=0.02, ndwi_p90=0.12)
        mid = dates[2]  # сцена в середине сезона
        for back in range(7):  # 25 мм за 7 суток к дате mid
            make_obs(db_session, fid, mid - timedelta(days=back), precip_mm=25 / 7)
        db_session.commit()
        body = get_insights(client, hdr, fid)
        it = item(body, "waterlogging")
        assert it["severity"] == "warn"  # последняя сцена чистая, доля мала
        assert mid.isoformat() in it["detail"]
        assert TODAY.isoformat() not in it["detail"]  # последняя — не эпизод

    def test_waterlogging_episode_on_latest_alert(self, world, db_session) -> None:
        """Эпизод на последней сцене — alert, даже если доля пикселей мала."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr, name="Мокрый финал")["field_id"])
        dates = [TODAY - timedelta(days=4 * back) for back in range(5)]
        for day in dates:
            make_scene(db_session, fid, day, 0.40, ndwi_mean=0.02, ndwi_p90=0.12)
        for back in range(7):  # дожди в окне последней сцены
            make_obs(db_session, fid, TODAY - timedelta(days=back), precip_mm=25 / 7)
        db_session.commit()
        body = get_insights(client, hdr, fid)
        assert item(body, "waterlogging")["severity"] == "alert"

    def test_waterlogging_silent_all_scenes_without_ndwi(self, world, db_session) -> None:
        """Ни у одной сцены нет NDWI (каталог до #83): эпизодов нет — вывода нет."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr, name="Без NDWI")["field_id"])
        for back in range(5):
            make_scene(db_session, fid, TODAY - timedelta(days=4 * back), 0.40)
        for back in range(30):
            make_obs(db_session, fid, TODAY - timedelta(days=back), precip_mm=5.0)
        db_session.commit()
        body = get_insights(client, hdr, fid)
        assert "waterlogging" not in codes(body["items"])

    def test_drought(self, world, db_session) -> None:
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY, 0.35)
        for back in range(14):  # 4 мм осадков, GDD ~12.5/сут (норма 10)
            make_obs(
                db_session, fid, TODAY - timedelta(days=back), precip_mm=0.3, t_min=14.0, t_max=19.0
            )
        db_session.commit()
        body = get_insights(client, hdr, fid)
        drought = item(body, "drought")
        assert drought["severity"] == "warn"
        assert "4.2 мм" in drought["detail"]

    def test_drought_silent_when_gdd_normal(self, world, db_session) -> None:
        """Сухо, но GDD в норме — не засуха по правилу (два условия ∧)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY, 0.35)
        for back in range(14):
            make_obs(
                db_session, fid, TODAY - timedelta(days=back), precip_mm=0.3, t_min=6.0, t_max=15.0
            )  # GDD (6+15)/2-5 = 5.5 < 10
        db_session.commit()
        body = get_insights(client, hdr, fid)
        assert "drought" not in codes(body["items"])

    def test_ndvi_lag_last_year(self, world, db_session) -> None:
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        last_year = TODAY.replace(year=TODAY.year - 1)
        make_scene(db_session, fid, last_year, 0.65)
        make_scene(db_session, fid, TODAY, 0.50)  # отставание 0.15 ≥ 0.1
        body = get_insights(client, hdr, fid)
        assert item(body, "ndvi_lag_last_year")["severity"] == "info"

    def test_agrochem_low(self, world, db_session) -> None:
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        db_session.add(Agrochem(field_id=fid, sample_date=date(2026, 9, 1), ph=4.9, humus_pct=1.8))
        db_session.commit()
        body = get_insights(client, hdr, fid)
        low = item(body, "agrochem_low")
        assert low["severity"] == "info"
        assert "pH 4.9" in low["detail"] and "1.8" in low["detail"]

    def test_agrochem_normal_silent(self, world, db_session) -> None:
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        db_session.add(Agrochem(field_id=fid, sample_date=date(2026, 9, 1), ph=6.2, humus_pct=3.4))
        db_session.commit()
        body = get_insights(client, hdr, fid)
        assert "agrochem_low" not in codes(body["items"])

    def test_data_refs_present(self, world, db_session) -> None:
        """data_refs ссылаются на сцены-участники детектора (03 §1.5)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        s1 = make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        s2 = make_scene(db_session, fid, TODAY, 0.50)
        body = get_insights(client, hdr, fid)
        refs = {ref["scene_id"] for ref in item(body, "ndvi_drop")["data_refs"]}
        assert refs == {str(s1.id), str(s2.id)}


class TestInsightsContract:
    def test_field_not_found_404(self, world) -> None:
        client, hdr = world
        resp = client.get(f"{API}/fields/{uuid.uuid4()}/insights", headers=hdr)
        assert resp.status_code == 404
        problem = resp.json()
        assert problem["code"] == "field_not_found"  # problem+json §5

    def test_no_ollama_source_rules_summary_null(self, world, db_session) -> None:
        """Деградация (REQ-NF-009): без Ollama items есть, summary=null."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY, 0.50)
        body = get_insights(client, hdr, fid)
        assert body["source"] == "rules"
        assert body["summary"] is None
        assert "ndvi_drop" in codes(body["items"])

    def test_cache_hit_same_response(self, world, db_session, monkeypatch) -> None:
        """Кэш 24 ч: повторный запрос — из кэша, generated_at не меняется."""
        import app.core.cache as cache_mod

        store: dict[str, str] = {}

        class FakeRedis:
            def get(self, key):
                return store.get(key)

            def setex(self, key, ttl, value):
                store[key] = value

        monkeypatch.setattr(cache_mod, "_redis", FakeRedis())

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY, 0.5)
        first = get_insights(client, hdr, fid)
        assert f"insights:{fid}:{TODAY.isoformat()}" in store
        # даже если данные изменились — кэш до новой сцены/TTL держит ответ
        make_scene(db_session, fid, TODAY - timedelta(days=3), 0.9)
        second = get_insights(client, hdr, fid)
        assert second["generated_at"] == first["generated_at"]
        assert second["items"] == first["items"]

    def test_new_scene_invalidates_cache(self, world, db_session, monkeypatch) -> None:
        """Новая чистая сцена — новый ключ: пересчёт items."""
        import app.core.cache as cache_mod

        store: dict[str, str] = {}

        class FakeRedis:
            def get(self, key):
                return store.get(key)

            def setex(self, key, ttl, value):
                store[key] = value

        monkeypatch.setattr(cache_mod, "_redis", FakeRedis())

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        first = get_insights(client, hdr, fid)
        assert "ndvi_drop" not in codes(first["items"])
        make_scene(db_session, fid, TODAY, 0.40)  # новая сцена + падение
        second = get_insights(client, hdr, fid)
        assert "ndvi_drop" in codes(second["items"])
        assert second["generated_at"] != first["generated_at"]

    def test_rights_machine_operator_forbidden(self, client, db_session) -> None:
        """Права как API-06: оператору техники (не группа satellite) — 403."""
        from app.models.auth import UserRole
        from tests.test_inputs import make_user

        hdr = headers(client, db_session)
        field = make_field(client, hdr)
        make_user(db_session, login="op_ins", role=UserRole.MACHINE_OPERATOR, password="pw-op")
        from tests.test_auth import auth_header

        op_hdr = auth_header(client, "op_ins", "pw-op")
        resp = client.get(f"{API}/fields/{field['field_id']}/insights", headers=op_hdr)
        assert resp.status_code == 403


class TestSummaryFallback:
    """_checked_summary: посторонние числа → конкатенация items (§8.2)."""

    def test_numbers_from_items_pass(self) -> None:
        from app.ai_assistant.schemas import InsightItem
        from app.ai_assistant.service import _checked_summary

        items = [
            InsightItem(
                code="ndvi_drop",
                severity="warn",
                title="Падение",
                detail="NDVI упал с 0.72 до 0.55: −0.17 за 5 суток.",
                data_refs=[],
            )
        ]
        assert _checked_summary("NDVI упал на 0.17 — проверьте поле.", items) is not None

    def test_alien_number_falls_back(self) -> None:
        from app.ai_assistant.schemas import InsightItem
        from app.ai_assistant.service import _checked_summary

        items = [
            InsightItem(
                code="ndvi_drop",
                severity="warn",
                title="Падение",
                detail="NDVI упал с 0.72 до 0.55: −0.17.",
                data_refs=[],
            )
        ]
        result = _checked_summary("Внесите 90 кг/га азота срочно!", items)
        assert result == "Падение: NDVI упал с 0.72 до 0.55: −0.17."


class TestBackgroundFix101:
    """#101: сессия БД не держится во время LLM-вызова; очередь LLM;
    доработка перезапускается, пока в кэше rules-ответ без сводки."""

    @staticmethod
    def _fake_redis(monkeypatch) -> dict[str, str]:
        import app.core.cache as cache_mod

        store: dict[str, str] = {}

        class FakeRedis:
            def get(self, key):
                return store.get(key)

            def setex(self, key, ttl, value):
                store[key] = value

        monkeypatch.setattr(cache_mod, "_redis", FakeRedis())
        return store

    def test_db_released_before_llm_and_cache_updated(self, world, db_session, monkeypatch):
        """На момент инференса пул не занят сессией фоновой задачи; параметры
        вызова LLM — timeout=300/attempts=1/keep_alive=30m; кэш дорабатывается."""
        from app.ai_assistant import service
        from sqlalchemy.orm import sessionmaker

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY, 0.50)
        db_session.commit()

        store = self._fake_redis(monkeypatch)
        test_engine = db_session.get_bind()
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=test_engine))
        state = {}

        def fake_summarize(prompt, context, **kwargs):
            state["checkedout"] = test_engine.pool.checkedout()
            state["kwargs"] = kwargs
            assert "ndvi_drop" in context
            return "NDVI упал на 0.22 — проверьте поле."

        monkeypatch.setattr(service.llm, "summarize", fake_summarize)
        db_session.close()  # собственная сессия теста тоже не держит пул

        service.refresh_insights(fid, TODAY)
        assert state["checkedout"] == 0  # соединение возвращено до LLM
        assert state["kwargs"]["timeout"] == 300.0
        assert state["kwargs"]["attempts"] == 1
        assert state["kwargs"]["keep_alive"] == "30m"

        import json as _json

        updated = _json.loads(store[f"insights:{fid}:{TODAY.isoformat()}"])
        assert updated["source"] == "rules+llm"
        assert updated["summary"] == "NDVI упал на 0.22 — проверьте поле."

    def test_llm_queue_timeout_defers_summary(self, world, db_session, monkeypatch):
        """Слот LLM занят и очередь истекла — задача уходит без инференса
        и без перезаписи кэша; сводка появится при повторном запросе."""
        from app.ai_assistant import service

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY, 0.50)
        db_session.commit()

        store = self._fake_redis(monkeypatch)
        from sqlalchemy.orm import sessionmaker

        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        monkeypatch.setattr(service, "LLM_QUEUE_TIMEOUT_SECONDS", 0.1)
        called = {"llm": False}
        monkeypatch.setattr(
            service.llm, "summarize", lambda *a, **k: called.__setitem__("llm", True)
        )
        db_session.close()

        assert service._llm_semaphore.acquire(timeout=1)  # слот занят «другой» задачей
        try:
            service.refresh_insights(fid, TODAY)  # не падает, тихо откладывается
        finally:
            service._llm_semaphore.release()
        assert called["llm"] is False
        assert f"insights:{fid}:{TODAY.isoformat()}" not in store

    def _get_with_ollama(self, client, monkeypatch, ollama="http://ollama:11434"):
        """GET /insights с включённым Ollama (settings через DI) и шпионом фона."""
        from app.ai_assistant import service
        from app.core.config import get_settings

        settings = get_settings().model_copy(update={"ollama_base_url": ollama})
        client.app.dependency_overrides[get_settings] = lambda: settings
        spy = {"n": 0}
        monkeypatch.setattr(
            service, "refresh_insights", lambda *a: spy.__setitem__("n", spy["n"] + 1)
        )
        return spy

    def test_rules_response_retried_on_next_get(self, world, db_session, monkeypatch):
        """Кэш с summary=null (доработка не успела) → повторный GET снова
        ставит фоновую задачу; готовая rules+llm-сводка — не ставит."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY, 0.50)  # drop → items непустые, summary=null
        db_session.commit()

        self._fake_redis(monkeypatch)
        spy = self._get_with_ollama(client, monkeypatch)
        from app.core.config import get_settings

        get_insights(client, hdr, fid)
        assert spy["n"] == 1  # свежий расчёт — фон запланирован
        get_insights(client, hdr, fid)  # кэш rules без сводки — повтор
        assert spy["n"] == 2

        import json as _json

        import app.core.cache as cache_mod

        key = f"insights:{fid}:{TODAY.isoformat()}"
        cached = cache_mod.cache_get(key)  # cache_get уже возвращает разобранный JSON
        cached["source"] = "rules+llm"
        cached["summary"] = "готово"
        cache_mod.cache_set(key, cached, 3600)
        get_insights(client, hdr, fid)
        assert spy["n"] == 2  # сводка есть — фон не нужен
        client.app.dependency_overrides.pop(get_settings, None)
