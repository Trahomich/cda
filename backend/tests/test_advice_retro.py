"""Тесты ретро-рекомендаций «на дату T» (#159, docs/09 §9).

Контекст as_of=T — только данные ≤ T (сцены, погода-факты, работы,
агрохимия), без прогноза; ключевые даты сезона по группе культуры
(озимые/яровые/пропашные/травы, укосы по провалам NDVI, сдвиг по плану);
идемпотентность generate_advice(as_of) и зачистка дублей --prune-today.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.ai_assistant import advice, advice_key_dates
from app.models.agronomy import CropCycle, FieldOperation
from app.models.ai import AIFieldAdvice
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from tests.test_advice import _anthropic_settings
from tests.test_inputs import make_culture, make_field
from tests.test_insights import make_obs, make_scene

SATELLITE_SOURCE = "satellite.sentinel2-l2a"
WEATHER_SOURCE = "weather.open-meteo"
T = date(2026, 6, 15)  # «дата ретро» — до конца сезона 2026


@pytest.fixture()
def world(client, db_session):
    """Как test_advice.world: агроном, источники (satellite+weather)."""
    from tests.test_inputs import headers

    hdr = headers(client, db_session)
    from app.models.references import Source

    db_session.add(Source(source_id=SATELLITE_SOURCE, kind="satellite"))
    db_session.add(Source(source_id=WEATHER_SOURCE, kind="weather"))
    db_session.commit()
    return client, hdr


def _retro_field(db: Session, client, hdr) -> uuid.UUID:
    """Поле сезона 2026: сцены до/после T, работы, агрохимия, погода."""
    fid = uuid.UUID(make_field(client, hdr, name="Ретроное")["field_id"])
    culture = make_culture(db, name="Ячмень")
    db.add(CropCycle(field_id=fid, year=2026, culture_id=culture.id, kind="main"))
    # сцены: три до T, две после (подглядывание недопустимо)
    make_scene(db, fid, date(2026, 5, 1), 0.30)
    make_scene(db, fid, date(2026, 6, 1), 0.55)
    make_scene(db, fid, date(2026, 6, 10), 0.60)
    make_scene(db, fid, date(2026, 8, 1), 0.75)  # > T
    make_scene(db, fid, date(2026, 10, 20), 0.41)  # > T
    # погода-факты вокруг T и после
    for i in range(5):
        make_obs(db, fid, T - timedelta(days=i), precip_mm=1.0, t_min=10.0, t_max=20.0)
    make_obs(db, fid, T + timedelta(days=5), precip_mm=99.0, t_min=1.0, t_max=3.0)
    db.commit()
    return fid


class TestRetroContext:
    def test_no_future_data_in_context(self, world, db_session):
        """Сцены/погода после T в контекст не попадают; есть пометка ретро."""
        client, hdr = world
        fid = _retro_field(db_session, client, hdr)

        ctx = advice.build_advice_context(db_session, fid, date(2026, 11, 5), as_of=T)
        assert ctx is not None
        assert f"Ретроспективная рекомендация на дату {T.isoformat()}" in ctx.context
        assert "2026-06-10" in ctx.context  # последняя сцена ≤ T
        assert "2026-08-01" not in ctx.context  # будущее не видно
        assert "2026-10-20" not in ctx.context
        assert "99" not in ctx.context  # осадки после T не видны
        assert "Прогноз погоды" not in ctx.context  # в ретро прогноза нет

    def test_live_context_still_has_forecast_and_future(self, world, db_session):
        """Без as_of поведение прежнее: сцены всего сезона + пометки ретро нет."""
        client, hdr = world
        fid = _retro_field(db_session, client, hdr)

        ctx = advice.build_advice_context(db_session, fid, date(2026, 11, 5))
        assert ctx is not None
        assert "2026-10-20" in ctx.context
        assert "Ретроспективная" not in ctx.context

    def test_plans_and_probe_cut_by_date(self, world, db_session):
        """Работы и агрохимия после T не видны в ретро-контексте."""
        from app.models.references import Agrochem

        client, hdr = world
        fid = _retro_field(db_session, client, hdr)
        db_session.add(Agrochem(field_id=fid, sample_date=date(2026, 7, 1), ph=5.0))
        db_session.add(
            FieldOperation(
                field_id=fid,
                operation_type="spraying",
                date=date(2026, 8, 2),
                actual_area_ga=10.0,
                status="done",
                source="manual",
            )
        )
        db_session.commit()

        ctx = advice.build_advice_context(db_session, fid, date(2026, 11, 5), as_of=T)
        assert ctx is not None
        assert "2026-08-02" not in ctx.context
        assert "проба 2026-07-01" not in ctx.context

    def test_season_year_is_last_before_t(self, world, db_session):
        """Сезон контекста — последний год со сценами ≤ T (2026)."""
        client, hdr = world
        fid = _retro_field(db_session, client, hdr)
        ctx = advice.build_advice_context(db_session, fid, date(2026, 11, 5), as_of=T)
        assert "сезон 2026" in ctx.context

    def test_before_any_scene_returns_none(self, world, db_session):
        """T раньше всех сцен — контекста нет (нет данных «на дату»)."""
        client, hdr = world
        fid = _retro_field(db_session, client, hdr)
        assert (
            advice.build_advice_context(db_session, fid, date(2026, 11, 5), as_of=date(2025, 1, 1))
            is None
        )


class TestKeyDates:
    def test_winter_season_spans_two_years(self, world, db_session):
        """Озимые, сезон 2026: перед посевом — 15.09.2025, уборка — 2026."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        kds = advice_key_dates.season_key_dates(db_session, fid, "Пшеница озимая", 2026)
        assert [(kd.label, kd.date) for kd in kds] == [
            ("перед посевом озимых", date(2025, 9, 15)),
            ("весенняя подкормка", date(2026, 4, 1)),
            ("перед уборкой", date(2026, 7, 1)),
        ]

    def test_spring_and_row_crop_defaults(self, world, db_session):
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        spring = advice_key_dates.season_key_dates(db_session, fid, "Ячмень", 2026)
        assert [kd.date for kd in spring] == [
            date(2026, 4, 25),
            date(2026, 6, 5),
            date(2026, 8, 1),
        ]
        corn = advice_key_dates.season_key_dates(db_session, fid, "Кукуруза на силос", 2026)
        assert [kd.date for kd in corn] == [
            date(2026, 5, 10),
            date(2026, 6, 15),
            date(2026, 9, 5),
        ]

    def test_grass_cut_dates_from_ndvi_dips(self, world, db_session):
        """Травы: старт отрастания + укосы по провалам NDVI ≥ 0.15."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        scenes = [
            (date(2026, 4, 20), 0.30),
            (date(2026, 5, 20), 0.80),  # пик → укос
            (date(2026, 5, 25), 0.35),  # провал 0.45 ≥ 0.15 → укос 05-28
            (date(2026, 6, 20), 0.75),
            (date(2026, 6, 25), 0.40),  # второй укос 06-28
        ]
        kds = advice_key_dates.season_key_dates(
            db_session, fid, "Многолетние травы", 2026, scenes=scenes
        )
        dates = [kd.date for kd in kds]
        assert date(2026, 4, 15) in dates
        assert date(2026, 5, 28) in dates
        assert date(2026, 6, 28) in dates

    def test_plan_date_shifts_key_date(self, world, db_session):
        """Есть реальный план того же типа в окне ±25 сут — дата плана − 3 сут."""
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        db_session.add(
            FieldOperation(
                field_id=fid,
                operation_type="seeding",
                date=date(2026, 5, 20),
                actual_area_ga=10.0,
                status="done",
                source="manual",
            )
        )
        db_session.commit()
        kds = advice_key_dates.season_key_dates(db_session, fid, "Кукуруза на силос", 2026)
        by_label = {kd.label: kd.date for kd in kds}
        assert by_label["перед посевом"] == date(2026, 5, 17)  # 20.05 − 3

    def test_season_cultures(self, world, db_session):
        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        c1 = make_culture(db_session, name="Гречиха")
        c2 = make_culture(db_session, name="Овёс")
        db_session.add(CropCycle(field_id=fid, year=2025, culture_id=c1.id, kind="main"))
        db_session.add(CropCycle(field_id=fid, year=2026, culture_id=c2.id, kind="main"))
        db_session.commit()
        seasons = advice_key_dates.season_cultures(db_session, fid, 2023, 2026)
        assert sorted(seasons) == [2025, 2026]
        assert seasons[2025].startswith("Гречиха")
        assert seasons[2026].startswith("Овёс")


class TestRetroGenerate:
    def test_advice_date_is_t_and_idempotent(self, world, db_session, monkeypatch):
        """generate_advice(as_of=T): advice_date=T; повтор с skip_existing — без новой записи."""
        client, hdr = world
        fid = _retro_field(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        monkeypatch.setattr(
            advice.llm,
            "summarize",
            lambda p, c, **k: f"На {T.isoformat()} поле в норме: NDVI 0.60, осадки 5.0 мм.",
        )
        monkeypatch.setattr(advice, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

        record = advice.generate_advice(fid, as_of=T)
        assert record is not None
        assert record.advice_date == T
        assert record.status == "ok"

        again = advice.generate_advice(fid, as_of=T, skip_existing=True)
        assert again is None  # идемпотентность
        rows = db_session.scalars(select(AIFieldAdvice).where(AIFieldAdvice.field_id == fid)).all()
        assert len(rows) == 1

    def test_retro_answer_guardrail_no_future_numbers(self, world, db_session, monkeypatch):
        """Guardrail: числа не из ретро-контекста (будущие) → fallback."""
        client, hdr = world
        fid = _retro_field(db_session, client, hdr)
        _anthropic_settings(monkeypatch)
        monkeypatch.setattr(advice, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
        # 0.75 — сцена 2026-08-01 (> T), в контексте её нет
        monkeypatch.setattr(advice.llm, "summarize", lambda p, c, **k: "Пик сезона 0.75!")

        record = advice.generate_advice(fid, as_of=T)
        assert record is not None
        assert record.status == "fallback"
        assert "0.75" not in record.answer


class TestPruneToday:
    def test_prune_keeps_latest_per_field(self, world, db_session, monkeypatch):
        """--prune-today: из дублей сегодняшних advice остаётся последняя запись поля."""
        import argparse

        from app import cli

        client, hdr = world
        fid = uuid.UUID(make_field(client, hdr)["field_id"])
        today = date.today()
        for i, mins in enumerate((1, 2, 3)):
            db_session.add(
                AIFieldAdvice(
                    field_id=fid,
                    model="glm-5.3-flash",
                    provider="anthropic",
                    advice_date=today,
                    answer=f"вариант {i}",
                    context_hash=f"h{i}",
                    status="ok",
                    created_at=datetime.now(UTC) + timedelta(minutes=mins),
                )
            )
        db_session.commit()
        monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

        code = cli.cmd_advice_refresh_backfill(argparse.Namespace(years=1, prune_today=True))
        assert code == 0
        rows = db_session.scalars(select(AIFieldAdvice).where(AIFieldAdvice.field_id == fid)).all()
        assert len(rows) == 1
        assert rows[0].answer == "вариант 2"
