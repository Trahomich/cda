"""Тесты AI-рекомендаций «что делать сейчас» (09-ai §9, #119).

Контекст §9.1 на демо-данных (все секции), guardrail §9.2 (выдуманное число
→ повтор → fallback на items §8.2), запись только ok/fallback, пустой сезон
и не-anthropic провайдер — без записи. LLM и сеть замоканы: наружу тесты
не ходят (httpx.MockTransport в test_llm; здесь — мок llm.summarize).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.ai_assistant import advice
from app.models.agronomy import CropCycle, FieldOperation, Recommendation
from app.models.ai import AIFieldAdvice
from app.models.integrations import Forecast, SatelliteScene
from app.models.references import Agrochem, Culture, Source
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from tests.test_inputs import headers, make_culture, make_fertilizer, make_field
from tests.test_insights import make_obs, make_scene

WEATHER_SOURCE = "weather.open-meteo"
SATELLITE_SOURCE = "satellite.sentinel2-l2a"
ADVICE_DAY = date(2026, 11, 5)  # «сегодня» для генерации (после сезона 2026)


@pytest.fixture()
def world(client, db_session):
    """Агроном, источники; ответ эндпоинта для поля без данных."""
    hdr = headers(client, db_session)
    db_session.add(Source(source_id=SATELLITE_SOURCE, kind="satellite"))
    db_session.add(Source(source_id=WEATHER_SOURCE, kind="weather"))
    db_session.commit()
    return client, hdr


def _season_world(db, client, hdr, *, culture_kwargs=None) -> tuple[uuid.UUID, Culture]:
    """Поле с полным набором данных сезона 2026 (контекст §9.1)."""
    fid = uuid.UUID(make_field(client, hdr, name="Советное")["field_id"])

    # культура с коэффициентами урожайности (§8.3)
    culture = make_culture(db, name="Гречиха")
    culture.yield_k_base = 10.0
    culture.yield_k_ndvi_max = 20.0
    culture.yield_k_ndvi_int = 5.0
    db.add(culture)

    # севооборот: 2026 гречиха, предшественник — цикл 2025 (пшеница)
    prev = CropCycle(
        field_id=fid,
        year=2025,
        culture_id=make_culture(db, name="Пшеница").id,
        kind="main",
    )
    db.add(prev)
    db.flush()
    db.add(
        CropCycle(
            field_id=fid,
            year=2026,
            culture_id=culture.id,
            kind="main",
            predecessor_cycle_id=prev.id,
        )
    )

    db.add(
        Agrochem(
            field_id=fid,
            sample_date=date(2026, 4, 1),
            ph=5.9,
            humus_pct=3.1,
            p2o5_mg100=12.0,
            k2o_mg100=9.5,
        )
    )

    fert = make_fertilizer(db)
    db.add(
        FieldOperation(
            field_id=fid,
            operation_type="fertilizing",
            date=date(2026, 5, 2),
            input_id=fert.id,
            actual_area_ga=10.0,
            source="manual",
            status="done",
        )
    )
    db.add(
        FieldOperation(
            field_id=fid,
            operation_type="spraying",
            date=date(2026, 11, 10),
            actual_area_ga=10.0,
            source="manual",
            status="planned",
        )
    )
    db.add(
        Recommendation(
            rule_id="RULE-AGRO-07",
            target_type="field",
            target_id=fid,
            action="Подкормка азотом в фазе кущения",
            rationale="дефицит N по агрохимии",
            data_refs=[],
        )
    )

    # NDVI-сезон 2026: пик 2026-07-01 (0.82), падение к 2026-10-20 (детектор
    # ndvi_drop: 0.72 → 0.41 за 19 суток), последняя чистая — 2026-10-20
    for d, avg in [
        (date(2026, 5, 1), 0.30),
        (date(2026, 6, 1), 0.55),
        (date(2026, 7, 1), 0.82),
        (date(2026, 8, 1), 0.70),
        (date(2026, 10, 1), 0.72),
        (date(2026, 10, 20), 0.41),
    ]:
        make_scene(db, fid, d, avg)

    # факт погоды 14 сут + прогноз 7 сут
    for i in range(14):
        make_obs(
            db,
            fid,
            ADVICE_DAY - timedelta(days=i),
            precip_mm=1.5,
            t_min=5.0,
            t_max=15.0,
        )
    for i in range(7):
        db.add(
            Forecast(
                field_id=fid,
                forecast_for=ADVICE_DAY + timedelta(days=i),
                issued_at=datetime.now(UTC),
                temperature_min_c=4.0,
                temperature_max_c=12.0,
                precipitation_mm=2.0,
                precip_prob_pct=40,
            )
        )
    db.commit()
    return fid, culture


def _anthropic_settings(monkeypatch, **update):
    """Настройки anthropic-провайдера (без сети: ключ тестовый)."""
    base = {"llm_provider": "anthropic", "anthropic_api_key": "test-key"}
    base.update(update)
    settings = type(advice.get_settings())(**base)
    monkeypatch.setattr(advice, "get_settings", lambda: settings)
    return settings


class TestBuildContext:
    def test_all_sections_present(self, world, db_session) -> None:
        """Полный контекст §9.1: все секции, хэш sha256, images пустой."""
        client, hdr = world
        fid, culture = _season_world(db_session, client, hdr)

        ctx = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx is not None
        assert "Советное" in ctx.context and "сезон 2026" in ctx.context  # поле
        assert "Гречиха" in ctx.context and "предшественник: Пшеница" in ctx.context
        assert "pH 5.9" in ctx.context and "K2O 9.5" in ctx.context
        assert "План работ" in ctx.context and "2026-11-10" in ctx.context
        assert "Журнал работ" in ctx.context and "2026-05-02" in ctx.context
        assert "2026-07-01: avg 0.820" in ctx.context and "[пик вегетации]" in ctx.context
        assert "факт 14 сут" in ctx.context and "Прогноз погоды на 7 сут" in ctx.context
        assert "Модель вымокания" in ctx.context
        assert "RULE-AGRO-07" in ctx.context
        assert "Модель дифвнесения: нет расчёта." in ctx.context
        assert "Прогноз урожайности" in ctx.context
        assert len(ctx.context_hash) == 64 and ctx.images == []

    def test_no_scenes_returns_none(self, world, db_session) -> None:
        """Поле без чистых сцен — пустой сезон: контекста нет (§9.3)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        assert advice.build_advice_context(db_session, fid, ADVICE_DAY) is None

    def test_vra_verdict_from_cache(self, world, db_session, monkeypatch) -> None:
        """§9.1 п.7: IQR + пространственный разброс из указателя пиков,
        число зон — из кэша зон; нет кэша зон — «нет расчёта зон»."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        pointer = {
            "date": "2026-07-01",
            "iqr_ndvi": 0.082,
            "spatial_spread": 0.12,
            "peaks": [
                {"date": "2026-06-01", "iqr_ndvi": 0.05, "spatial_spread": 0.1},
                {"date": "2026-07-01", "iqr_ndvi": 0.082, "spatial_spread": 0.12},
            ],
        }
        zones_cache = {"zones": [{}, {}, {}, {}]}  # 4 зоны дифвнесения

        def fake_cache_get(key: str):
            if key == f"vra:peak:{fid}:2026":
                return pointer
            if key == f"vra:{fid}:2026-07-01":
                return zones_cache
            return None

        monkeypatch.setattr(advice, "cache_get", fake_cache_get)
        ctx = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx is not None
        assert "пиковые сцены 2026-06-01, 2026-07-01" in ctx.context
        assert "IQR NDVI = 0.082" in ctx.context
        assert "пространственный разброс = 0.120" in ctx.context
        assert "зон дифвнесения 4 (/vra)" in ctx.context

        def fake_no_zones(key: str):
            return pointer if key == f"vra:peak:{fid}:2026" else None

        monkeypatch.setattr(advice, "cache_get", fake_no_zones)
        ctx2 = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx2 is not None
        assert "нет расчёта зон" in ctx2.context

    def test_render_images_read_from_disk(self, world, db_session, tmp_path, monkeypatch) -> None:
        """Файловые NDVI-рендеры §7.3 (последняя + пик) попадают в images (≤2)."""
        from app.services.sentinel_ingest.renders import render_path

        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch, ndvi_render_dir=str(tmp_path))
        png = b"\x89PNG-fake"
        for day in (date(2026, 10, 20), date(2026, 7, 1)):  # последняя + пик
            p = render_path(tmp_path, fid, "ndvi", day)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(png)

        ctx = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx is not None and ctx.images == [png, png]

        # одна и та же сцена — последняя и пик: одна картинка
        ctx1 = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx1 is not None
        peak_only = date(2026, 7, 1)
        p = render_path(tmp_path, fid, "ndvi", peak_only)
        p.unlink()
        ctx2 = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx2 is not None and len(ctx2.images) == 1


class TestGenerateAdvice:
    def _patch_db(self, monkeypatch, db_session):
        """generate_advice работает на тестовом движке (как TestBackgroundFix101)."""
        monkeypatch.setattr(advice, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    def test_ok_answer_stored(self, world, db_session, monkeypatch) -> None:
        """Числа из context — статус ok, запись в ai_field_advice."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch, llm_model="glm-5.3-flash")
        self._patch_db(monkeypatch, db_session)

        good = "Проверьте поле: NDVI 0.410 на 2026-10-20. Окно работ после 2026-11-10."
        monkeypatch.setattr(advice.llm, "summarize", lambda *a, **k: good)
        db_session.close()

        record = advice.generate_advice(fid, today=ADVICE_DAY)
        assert record is not None and record.status == "ok"
        assert record.provider == "anthropic" and record.model == "glm-5.3-flash"
        assert record.advice_date == ADVICE_DAY and record.answer == good
        assert len(record.context_hash) == 64

    def test_guardrail_fallback_to_items(self, world, db_session, monkeypatch) -> None:
        """Выдуманное число и после повтора — fallback на конкатенацию items §8.2."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        self._patch_db(monkeypatch, db_session)

        bad = "Внесите 999 кг/га селитры немедленно."  # 999 нет в context
        calls = {"n": 0}

        def fake_summarize(*a, **k):
            calls["n"] += 1
            return bad

        monkeypatch.setattr(advice.llm, "summarize", fake_summarize)
        db_session.close()

        record = advice.generate_advice(fid, today=ADVICE_DAY)
        assert calls["n"] == 2  # первый ответ + повтор с уточнением (§9.2)
        assert record is not None and record.status == "fallback"
        assert "999" not in record.answer
        # items §8.2 на этих данных: NDVI-падение с пика 0.820
        assert "NDVI" in record.answer

    def test_guardrail_retry_success(self, world, db_session, monkeypatch) -> None:
        """Повтор с уточнением прошёл guardrail — статус ok, один вызов ×2."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        self._patch_db(monkeypatch, db_session)

        answers = iter(
            ["Проверьте всё по норме 777 кг.", "Проверьте поле: NDVI 0.410 (2026-10-20)."]
        )
        monkeypatch.setattr(advice.llm, "summarize", lambda *a, **k: next(answers))
        db_session.close()

        record = advice.generate_advice(fid, today=ADVICE_DAY)
        assert record is not None and record.status == "ok"
        assert "777" not in record.answer

    def test_empty_season_no_record(self, world, db_session, monkeypatch) -> None:
        """Поле без сцен: генерации нет, записи нет (empty не пишем, §9.3)."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        _anthropic_settings(monkeypatch)
        self._patch_db(monkeypatch, db_session)
        called = {"llm": False}
        monkeypatch.setattr(
            advice.llm, "summarize", lambda *a, **k: called.__setitem__("llm", True)
        )
        db_session.close()

        assert advice.generate_advice(fid, today=ADVICE_DAY) is None
        assert called["llm"] is False

    def test_provider_off_no_record(self, world, db_session, monkeypatch) -> None:
        """LLM_PROVIDER=ollama (дефолт) — advice не формируется (§9 деградация)."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        settings = type(advice.get_settings())(llm_provider="ollama")
        monkeypatch.setattr(advice, "get_settings", lambda: settings)
        self._patch_db(monkeypatch, db_session)
        called = {"llm": False}
        monkeypatch.setattr(
            advice.llm, "summarize", lambda *a, **k: called.__setitem__("llm", True)
        )
        db_session.close()

        assert advice.generate_advice(fid, today=ADVICE_DAY) is None
        assert called["llm"] is False
        # тот же дефолт у worker-очереди: ничего не падает
        advice.queue_advice(fid)

    def test_llm_unavailable_no_record(self, world, db_session, monkeypatch) -> None:
        """Провайдер недоступен (None) — записи нет (§9.5)."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        self._patch_db(monkeypatch, db_session)
        monkeypatch.setattr(advice.llm, "summarize", lambda *a, **k: None)
        db_session.close()

        assert advice.generate_advice(fid, today=ADVICE_DAY) is None

    def test_images_passed_only_to_anthropic_llm(
        self, world, db_session, tmp_path, monkeypatch
    ) -> None:
        """Рендеры передаются в llm.summarize как images (anthropic, §9.1)."""
        from app.services.sentinel_ingest.renders import render_path

        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch, ndvi_render_dir=str(tmp_path))
        self._patch_db(monkeypatch, db_session)
        png = b"\x89PNG-fake"
        p = render_path(tmp_path, fid, "ndvi", date(2026, 10, 20))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png)
        seen: dict = {}

        def fake_summarize(prompt, context, **kwargs):
            seen.update(kwargs)
            return "Проверьте поле: NDVI 0.410 (2026-10-20)."

        monkeypatch.setattr(advice.llm, "summarize", fake_summarize)
        db_session.close()

        record = advice.generate_advice(fid, today=ADVICE_DAY)
        assert record is not None and record.status == "ok"
        assert seen.get("images") == [png]
        assert seen.get("provider") == "anthropic"
        assert seen.get("timeout") == advice.ADVICE_LLM_TIMEOUT_SECONDS


class TestQueue:
    def test_queue_advice_runs_and_records(self, world, db_session, monkeypatch) -> None:
        """queue_advice (§9.4): фоновый worker пишет запись, исключения гасятся."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        monkeypatch.setattr(advice, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        monkeypatch.setattr(
            advice.llm,
            "summarize",
            lambda *a, **k: "Проверьте поле: NDVI 0.410 (2026-10-20).",
        )
        db_session.close()

        advice.queue_advice(fid, today=ADVICE_DAY)
        pool = advice._worker_pool()  # noqa: SLF001
        pool.shutdown(wait=True)
        advice._pool = None  # noqa: SLF001 — вернуть очередь для других тестов

        rows = db_session.scalars(select(AIFieldAdvice).where(AIFieldAdvice.field_id == fid)).all()
        assert len(rows) == 1 and rows[0].status == "ok"


class TestStripMarkdown131:
    """#131: advice.answer — простой текст без markdown (§9.2): запрет в
    промпте + strip_markdown после guardrail."""

    def test_prompt_forbids_markdown(self) -> None:
        assert "без markdown-разметки" in advice.ADVICE_PROMPT

    def test_answer_sanitized_on_store(self, world, db_session, monkeypatch) -> None:
        """Мок-LLM вернула ответ с **/#/` — в ai_field_advice записан чистый
        текст, легальные числа сохранены."""
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        TestGenerateAdvice()._patch_db(monkeypatch, db_session)

        md_answer = (
            "# Рекомендации\n**Проверьте поле:** NDVI 0.410 на 2026-10-20.\n"
            "* осмотреть север\n* окно работ после 2026-11-10"
        )
        monkeypatch.setattr(advice.llm, "summarize", lambda *a, **k: md_answer)
        db_session.close()

        record = advice.generate_advice(fid, today=ADVICE_DAY)
        assert record is not None and record.status == "ok"
        assert record.answer == (
            "Рекомендации\nПроверьте поле: NDVI 0.410 на 2026-10-20.\n"
            "- осмотреть север\n- окно работ после 2026-11-10"
        )
        for md in ("**", "`", "\n#"):
            assert md not in record.answer
        assert "0.410" in record.answer


class TestExtraIndicesContext:
    """Секция «Другие индексы сезона» в контексте advice (§9.1, #153)."""

    @staticmethod
    def _index_scene(db, fid, index, day, avg):
        from datetime import date as d

        db.add(
            SatelliteScene(
                field_id=fid,
                index=index,
                captured_at=d.fromisoformat(day),
                resolution_m=10,
                index_min=avg - 0.1,
                index_avg=avg,
                index_max=avg + 0.1,
                cloud_pct=0.0,
                composite_days=1,
                source_id=SATELLITE_SOURCE,
                trust=1.0,
            )
        )

    def test_section_present(self, world, db_session) -> None:
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        self._index_scene(db_session, fid, "ndre", "2026-07-01", 0.42)
        self._index_scene(db_session, fid, "ndre", "2026-06-11", 0.38)
        self._index_scene(db_session, fid, "ndmi", "2026-07-01", -0.12)
        self._index_scene(db_session, fid, "evi", "2026-05-01", 0.51)
        self._index_scene(db_session, fid, "bsi", "2026-07-01", -0.2)
        # зимний NDSI сезона 2026: декабрь 2025
        self._index_scene(db_session, fid, "ndsi", "2025-12-20", 0.6)
        # чужой сезон и вне месяцев (ноябрьская evi) — не попадают
        self._index_scene(db_session, fid, "ndre", "2024-07-01", 0.5)
        self._index_scene(db_session, fid, "evi", "2026-11-20", 0.4)
        db_session.commit()

        ctx = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx is not None
        assert "Другие индексы сезона" in ctx.context
        assert "NDRE (азотный статус): 2026-06-11: 0.380, 2026-07-01: 0.420" in ctx.context
        assert "NDMI (влажность биомассы): 2026-07-01: -0.120" in ctx.context
        assert "EVI (вегетация): 2026-05-01: 0.510" in ctx.context
        assert "BSI (голая почва): 2026-07-01: -0.200" in ctx.context
        assert "NDSI (снег): 2025-12-20: 0.600" in ctx.context
        assert "2024-07-01" not in ctx.context and "2026-11-20" not in ctx.context

    def test_section_absent_without_indices(self, world, db_session) -> None:
        client, hdr = world
        fid, _ = _season_world(db_session, client, hdr)
        ctx = advice.build_advice_context(db_session, fid, ADVICE_DAY)
        assert ctx is not None
        assert "Другие индексы сезона" not in ctx.context
