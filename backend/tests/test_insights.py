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
from app.ai_assistant.service import _key as insights_key
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
        assert insights_key(fid, TODAY) in store
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


class TestLlmContext105:
    """#105: контекст LLM — NDVI-динамика, вердикты моделей; guardrail ± контекст."""

    def test_context_ndvi_series_and_verdicts(self, world, db_session) -> None:
        """Контекст содержит NDVI-серию, min/max сезона и вердикты моделей."""
        from app.ai_assistant import service
        from app.models.agronomy import Field

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        for i, ndvi in enumerate((0.30, 0.55, 0.72)):
            make_scene(db_session, fid, date(2026, 5, 1) + timedelta(days=10 * i), ndvi)
        db_session.commit()
        field = db_session.get(Field, fid)
        assert field is not None
        verdict = "Модель дифвнесения: поле однородно, IQR NDVI = 0.058 — зоны не строятся."
        ctx = service._llm_context(db_session, field, [], vra_verdict=verdict)
        assert "NDVI-динамика сезона (последние 3 чистых сцен" in ctx
        assert "2026-05-01: 0.300" in ctx and "2026-05-21: 0.720" in ctx
        assert "min 0.300 / max 0.720" in ctx
        # сцен с NDWI и погодой нет — проверено 0 сцен
        assert "Модель вымокания: признаков вымокания нет по 0 сценам." in ctx
        assert verdict in ctx

    def test_context_waterlogging_episodes(self, world, db_session) -> None:
        """Эпизод вымокания (NDWI + осадки) попадает в вердикт модели датами."""
        from app.ai_assistant import service
        from app.models.agronomy import Field

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, date(2026, 6, 10), 0.40, ndwi_mean=0.12, ndwi_p90=0.15)
        for back in range(7):  # ≥ 20 мм за 7 суток
            make_obs(db_session, fid, date(2026, 6, 10) - timedelta(days=back), precip_mm=4.0)
        db_session.commit()
        field = db_session.get(Field, fid)
        assert field is not None
        ctx = service._llm_context(db_session, field, [])
        assert "Модель вымокания: эпизоды по сценам 2026-06-10." in ctx

    def test_checked_summary_allows_context_numbers(self) -> None:
        """Число из контекста (IQR) не считается выдуманным — сводка проходит."""
        from app.ai_assistant.schemas import InsightItem
        from app.ai_assistant.service import _checked_summary

        items = [
            InsightItem(
                code="ndvi_drop",
                severity="warn",
                title="Падение",
                detail="NDVI упал с 0.72 до 0.50: −0.22.",
                data_refs=[],
            )
        ]
        context = "NDVI-динамика: min 0.300 / max 0.720. IQR NDVI = 0.145, 3 зон дифвнесения."
        summary = "IQR NDVI = 0.145 — стоит построить зоны дифвнесения."
        assert _checked_summary(summary, items, context) == summary

    def test_checked_summary_alien_number_still_falls_back(self) -> None:
        """Постороннего числа нет ни в items, ни в контексте — fallback."""
        from app.ai_assistant.schemas import InsightItem
        from app.ai_assistant.service import _checked_summary

        items = [
            InsightItem(
                code="ndvi_drop",
                severity="warn",
                title="Падение",
                detail="NDVI упал с 0.72 до 0.50: −0.22.",
                data_refs=[],
            )
        ]
        result = _checked_summary("Внесите 90 кг/га азота срочно!", items, "IQR NDVI = 0.145.")
        assert result == "Падение: NDVI упал с 0.72 до 0.50: −0.22."

    def test_refresh_passes_context_with_verdicts(self, world, db_session, monkeypatch) -> None:
        """refresh_insights передаёт LLM контекст с NDVI-серией и вердиктами
        моделей (VRA без ассетов → «нет расчёта»)."""
        from app.ai_assistant import service
        from sqlalchemy.orm import sessionmaker

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY, 0.50)
        db_session.commit()

        TestBackgroundFix101._fake_redis(monkeypatch)
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        captured: dict[str, str] = {}

        def fake_summarize(prompt, context, **kwargs):
            captured["context"] = context
            return "NDVI упал на 0.22 — проверьте поле."

        monkeypatch.setattr(service.llm, "summarize", fake_summarize)
        db_session.close()

        service.refresh_insights(fid, TODAY)
        ctx = captured["context"]
        assert "NDVI-динамика сезона" in ctx
        assert "Модель вымокания" in ctx
        assert service.VRA_NO_DATA_VERDICT in ctx


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
        вызова LLM — timeout=600/attempts=1/keep_alive=30m/num_predict=256
        (#112); кэш дорабатывается."""
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
        assert state["kwargs"]["timeout"] == 600.0
        assert state["kwargs"]["attempts"] == 1
        assert state["kwargs"]["keep_alive"] == "30m"
        assert state["kwargs"]["num_predict"] == 256  # лимит генерации (#112)

        import json as _json

        updated = _json.loads(store[insights_key(fid, TODAY)])
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
        assert insights_key(fid, TODAY) not in store

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

        import app.core.cache as cache_mod

        key = insights_key(fid, TODAY)
        cached = cache_mod.cache_get(key)  # cache_get уже возвращает разобранный JSON
        cached["source"] = "rules+llm"
        cached["summary"] = "готово"
        cache_mod.cache_set(key, cached, 3600)
        get_insights(client, hdr, fid)
        assert spy["n"] == 2  # сводка есть — фон не нужен
        client.app.dependency_overrides.pop(get_settings, None)


class TestVisionRenders117:
    """#117: vision-сводка — рендеры поля уходят в LLM только при
    anthropic-провайдере и наличии файлов; guardrail §8.2 не ослабляется."""

    @staticmethod
    def _anthropic(monkeypatch, **update):
        from app.ai_assistant import service

        base = {"llm_provider": "anthropic", "anthropic_api_key": "test-key"}
        base.update(update)
        settings = type(service.get_settings())(**base)
        monkeypatch.setattr(service, "get_settings", lambda: settings)
        return service

    @staticmethod
    def _prepare(world, db_session, monkeypatch):
        from app.ai_assistant import service
        from sqlalchemy.orm import sessionmaker

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.72)
        make_scene(db_session, fid, TODAY, 0.50)  # ndvi_drop → items непустые
        db_session.commit()
        TestBackgroundFix101._fake_redis(monkeypatch)
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        db_session.close()
        return fid

    def test_anthropic_renders_passed(self, world, db_session, monkeypatch, tmp_path):
        """Anthropic + файловые рендеры: VISION_PROMPT, подпись в context,
        PNG последней чистой сцены (ndvi/truecolor/ndwi) в images."""
        from app.services.sentinel_ingest.renders import render_path

        service = self._anthropic(monkeypatch, ndvi_render_dir=str(tmp_path))
        fid = self._prepare(world, db_session, monkeypatch)
        for variant in ("ndvi", "truecolor", "ndwi"):
            path = render_path(tmp_path, fid, variant, TODAY)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x89PNG-fake")

        calls: dict = {}

        def fake_summarize(prompt, context, **kwargs):
            calls.update(prompt=prompt, context=context, kwargs=kwargs)
            return "NDVI упал на 0.22 — на снимках видно пятно в северной части."

        monkeypatch.setattr(service.llm, "summarize", fake_summarize)
        service.refresh_insights(fid, TODAY)

        assert calls["prompt"] == service.VISION_PROMPT
        assert (
            f"Приложены рендеры поля (изображения): ndvi за {TODAY.isoformat()}"
            in (calls["context"])
        )
        assert "truecolor" in calls["context"] and "ndwi" in calls["context"]
        images = calls["kwargs"]["images"]
        assert images is not None and len(images) == 3
        assert all(png == b"\x89PNG-fake" for png in images)

    def test_peak_render_appended(self, world, db_session, monkeypatch, tmp_path):
        """Рендер NDVI пика вегетации добавляется к трём вариантам
        последней сцены (всего ≤ VISION_MAX_IMAGES)."""
        from datetime import date as _date

        from app.services.sentinel_ingest.renders import render_path

        peak_day = _date(2026, 7, 1)
        service = self._anthropic(monkeypatch, ndvi_render_dir=str(tmp_path))
        fid = self._prepare(world, db_session, monkeypatch)

        class _FakeZones:  # заглушка результата vra_zones (без COG)
            captured_at = peak_day
            iqr_ndvi = 0.1
            spatial_spread = 0.1
            zones = []

        monkeypatch.setattr(service.vra, "vra_zones", lambda db, field, *, year=None: _FakeZones())
        for day, variants in ((TODAY, ("ndvi", "truecolor", "ndwi")), (peak_day, ("ndvi",))):
            for variant in variants:
                path = render_path(tmp_path, fid, variant, day)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"png")

        calls: dict = {}
        monkeypatch.setattr(
            service.llm, "summarize", lambda p, c, **k: calls.update(k) or "ok 0.22"
        )
        service.refresh_insights(fid, TODAY)
        assert calls["images"] is not None and len(calls["images"]) == 4

    def test_ollama_no_images(self, world, db_session, monkeypatch, tmp_path):
        """Ollama-провайдер — деградация: текстовый PROMPT, images не
        передаются (текущее поведение §8.2 до #117)."""
        from app.ai_assistant import service

        settings = type(service.get_settings())(
            llm_provider="ollama",
            ollama_base_url="http://ollama:11434",
            ndvi_render_dir=str(tmp_path),
        )
        monkeypatch.setattr(service, "get_settings", lambda: settings)
        fid = self._prepare(world, db_session, monkeypatch)
        calls: dict = {}
        monkeypatch.setattr(
            service.llm, "summarize", lambda p, c, **k: calls.update(prompt=p, kwargs=k) or "ok"
        )
        service.refresh_insights(fid, TODAY)
        assert calls["prompt"] == service.PROMPT
        assert calls["kwargs"].get("images") is None

    def test_no_render_files_text_prompt(self, world, db_session, monkeypatch, tmp_path):
        """Anthropic, но файлов рендеров нет (render_dir пуст) — текстовый
        PROMPT без изображений."""
        service = self._anthropic(monkeypatch, ndvi_render_dir=str(tmp_path))
        fid = self._prepare(world, db_session, monkeypatch)
        calls: dict = {}
        monkeypatch.setattr(
            service.llm, "summarize", lambda p, c, **k: calls.update(prompt=p, kwargs=k) or "ok"
        )
        service.refresh_insights(fid, TODAY)
        assert calls["prompt"] == service.PROMPT
        assert calls["kwargs"].get("images") is None

    def test_vision_guardrail_not_weakened(self, world, db_session, monkeypatch, tmp_path):
        """Vision-сводка с посторонним числом — fallback на items §8.2
        (guardrail общий, oслабления нет)."""
        from app.services.sentinel_ingest.renders import render_path

        service = self._anthropic(monkeypatch, ndvi_render_dir=str(tmp_path))
        fid = self._prepare(world, db_session, monkeypatch)
        path = render_path(tmp_path, fid, "ndvi", TODAY)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        monkeypatch.setattr(service.llm, "summarize", lambda p, c, **k: "Внесите 90 кг/га!")
        service.refresh_insights(fid, TODAY)

        from app.core.cache import cache_get  # уже отдаёт разобранный JSON

        updated = cache_get(insights_key(fid, TODAY))
        # выдуманное число отброшено, опубликован fallback из items §8.2
        assert updated["summary"].startswith("Резкое падение NDVI")
        assert "90" not in updated["summary"]


class TestStripMarkdown131:
    """#131: LLM-вывод — простой текст без markdown (§8.2): запрет в промпте +
    санитайзер strip_markdown после guardrail."""

    def test_sanitizer_unit(self) -> None:
        """Юнит: разметка срезается, чистый текст не меняется, числа целы."""
        from app.ai_assistant.service import strip_markdown

        assert strip_markdown("Обычный текст. NDVI 0.41.") == "Обычный текст. NDVI 0.41."
        assert strip_markdown("**Важно:** проверить поле.") == "Важно: проверить поле."
        assert strip_markdown("_курсив_ и __жирный__") == "курсив и жирный"
        assert strip_markdown("### Заголовок\nТекст.") == "Заголовок\nТекст."
        assert strip_markdown("`код` и ```\nблок\n```конец") == "код и блок\nконец"
        assert strip_markdown("* пункт\n* пункт два") == "- пункт\n- пункт два"
        # числа со звёздочкой/подчёркиванием (умножение, snake_case) не трогаем
        assert strip_markdown("2*3*4 и some_name") == "2*3*4 и some_name"

    def test_prompt_forbids_markdown(self) -> None:
        """Промпты §8.2/§8.5 требуют простой текст без markdown."""
        from app.ai_assistant import service

        for prompt in (service.PROMPT, service.VISION_PROMPT):
            assert "без markdown-разметки" in prompt

    def test_summary_sanitized_in_cache(self, world, db_session, monkeypatch, tmp_path):
        """Мок-LLM вернула сводку с **/#/` — в кэше опубликован чистый текст
        (легальные числа сохранены, разметки нет)."""
        service = TestVisionRenders117._anthropic(monkeypatch, ndvi_render_dir=str(tmp_path))
        fid = TestVisionRenders117._prepare(world, db_session, monkeypatch)
        md_answer = "# Сводка по полю\n**NDVI** упал на 0.22 — `проверить` север."
        monkeypatch.setattr(service.llm, "summarize", lambda p, c, **k: md_answer)
        service.refresh_insights(fid, TODAY)

        from app.core.cache import cache_get  # уже отдаёт разобранный JSON

        updated = cache_get(insights_key(fid, TODAY))
        summary = updated["summary"]
        assert summary.startswith("Сводка по полю\nNDVI упал на 0.22 — проверить север.")
        assert "**" not in summary and "`" not in summary and "\n#" not in summary
        assert "0.22" in summary


class TestLlmVerdictEveryField138:
    """#138 (§8.2 v1.3): LLM-вердикт обязателен по каждому полю, включая
    items=[]; статическая строка «отклонений не выявлено» — только
    деградация при недоступном LLM; кэш — ключ версии v2."""

    @staticmethod
    def _quiet_field(world, db_session):
        """Поле со стабильным NDVI (детекторы молчат, items=[])."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        make_scene(db_session, fid, TODAY - timedelta(days=6), 0.55)
        make_scene(db_session, fid, TODAY, 0.56)
        db_session.commit()
        return fid

    def test_llm_called_with_empty_items(self, world, db_session, monkeypatch):
        """refresh_insights при items=[] зовёт LLM с непустым контекстом
        и публикует rules+llm; промпты требуют вердикт без пустых ответов."""
        from app.ai_assistant import service
        from sqlalchemy.orm import sessionmaker

        client, hdr = world
        fid = self._quiet_field(world, db_session)
        assert service.build_items(db_session, fid) == []

        store = TestBackgroundFix101._fake_redis(monkeypatch)
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        captured: dict[str, str] = {}
        monkeypatch.setattr(
            service.llm,
            "summarize",
            lambda p, c, **k: (
                captured.update(prompt=p, context=c)
                or "Посев в норме: вегетация идёт ровно, следите за динамикой."
            ),
        )
        db_session.close()

        service.refresh_insights(fid, TODAY)
        assert "не повод для пустого ответа" in captured["prompt"]
        assert "NDVI-динамика сезона" in captured["context"]  # контекст не пуст

        import json as _json

        updated = _json.loads(store[insights_key(fid, TODAY)])
        assert updated["source"] == "rules+llm"
        assert updated["summary"].startswith("Посев в норме")

    def test_degradation_static_when_llm_fails(self, world, db_session, monkeypatch):
        """Провайдер недоступен (None) и items=[] — статическая строка
        «отклонений не выявлено», source=rules (§8.2 v1.3)."""
        from app.ai_assistant import service
        from sqlalchemy.orm import sessionmaker

        client, hdr = world
        fid = self._quiet_field(world, db_session)

        store = TestBackgroundFix101._fake_redis(monkeypatch)
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        monkeypatch.setattr(service.llm, "summarize", lambda p, c, **k: None)
        db_session.close()

        service.refresh_insights(fid, TODAY)
        import json as _json

        updated = _json.loads(store[insights_key(fid, TODAY)])
        assert updated["source"] == "rules"
        assert updated["summary"] == service.NO_DEVIATIONS_SUMMARY

    def test_guardrail_empty_items_invented_numbers(self, world, db_session, monkeypatch):
        """items=[] + выдуманные числа → guardrail отверг сводку, fallback
        пуст → деградация статической строкой (не пустой текст)."""
        from app.ai_assistant import service
        from sqlalchemy.orm import sessionmaker

        client, hdr = world
        fid = self._quiet_field(world, db_session)

        store = TestBackgroundFix101._fake_redis(monkeypatch)
        monkeypatch.setattr(service, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        monkeypatch.setattr(service.llm, "summarize", lambda p, c, **k: "Внесите 90 кг/га азота!")
        db_session.close()

        service.refresh_insights(fid, TODAY)
        import json as _json

        updated = _json.loads(store[insights_key(fid, TODAY)])
        assert updated["source"] == "rules"
        assert updated["summary"] == service.NO_DEVIATIONS_SUMMARY

    def test_sync_static_when_llm_disabled(self, world, db_session):
        """LLM выключен (default) и items=[] — синхронный ответ содержит
        статическую строку деградации, source=rules."""
        client, hdr = world
        fid = self._quiet_field(world, db_session)
        resp = get_insights(client, hdr, fid)
        assert resp["source"] == "rules"
        assert resp["items"] == []
        assert resp["summary"] == "Отклонений по данным детекторов не выявлено."

    def test_bg_scheduled_with_empty_items(self, world, db_session, monkeypatch):
        """llm_enabled + items=[] → фоновая доработка планируется (раньше
        short-circuit не ставил задачу)."""
        client, hdr = world
        fid = self._quiet_field(world, db_session)
        spy = TestBackgroundFix101()._get_with_ollama(client, monkeypatch)
        resp = get_insights(client, hdr, fid)
        assert resp["items"] == []
        assert spy["n"] == 1
        client.app.dependency_overrides.clear()

    def test_cache_key_versioned(self):
        """Ключ кэша — v2 (#138): поднятие версии сбрасывает старые кэши
        со статическими сводками."""
        from app.ai_assistant import service

        fid = uuid.UUID("c3b625bd-523d-42e8-8ab8-b7d60281ab06")
        assert service._key(fid, date(2026, 10, 31)) == (
            f"insights:v{service.INSIGHTS_CACHE_VERSION}:{fid}:2026-10-31"
        )
        assert service.INSIGHTS_CACHE_VERSION == 2
