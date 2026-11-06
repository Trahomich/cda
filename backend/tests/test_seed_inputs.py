"""Тесты сида справочника препаратов (v2-01, #71) — docs/06 §7.2, docs/03 §1.4.

seed_inputs: ≥30 позиций (удобрения + СЗР); у удобрений заполнен NPK и
waiting_days NULL (RULE-AGRO-08), у СЗР — действующее вещество, концентрация
и срок ожидания; идемпотентность ON CONFLICT (kind, name); approved_crops
(RULE-AGRO-10) резолвятся по именам культур ref_culture, отсутствующие —
не ошибка.
"""

from sqlalchemy import select

from app.models.agronomy import AgroInput
from app.models.references import Culture
from app.seed import seed_references
from app.seed_inputs import seed_inputs


def make_cultures(db_session) -> None:
    """Культуры для резолва approved_crops (реф. сид создаёт базовый набор)."""
    seed_references(db_session)


def all_inputs(db_session) -> list[AgroInput]:
    return list(db_session.scalars(select(AgroInput)).all())


def test_seed_inputs_min_size_and_split(db_session) -> None:
    """≥30 позиций, из них ≥12 удобрений и ≥18 СЗР."""
    report = seed_inputs(db_session)
    rows = all_inputs(db_session)
    assert len(rows) >= 30
    assert report.inserted == len(rows)
    assert report.existing == 0
    kinds = {r.kind for r in rows}
    assert kinds == {"fertilizer", "ppp"}
    assert sum(1 for r in rows if r.kind == "fertilizer") >= 12
    assert sum(1 for r in rows if r.kind == "ppp") >= 18


def test_seed_inputs_fertilizer_fields(db_session) -> None:
    """Удобрения: NPK заполнен, поля СЗР NULL, waiting_days NULL (RULE-AGRO-08)."""
    seed_inputs(db_session)
    fertilizers = [r for r in all_inputs(db_session) if r.kind == "fertilizer"]
    assert fertilizers
    for row in fertilizers:
        assert row.n_pct is not None
        assert row.p2o5_pct is not None
        assert row.k2o_pct is not None
        assert row.active_substance is None
        assert row.concentration is None
        assert row.waiting_days is None  # ck_dat_agro_input_waiting_days
        assert row.unit in ("kg_ha", "l_ha")


def test_seed_inputs_ppp_fields(db_session) -> None:
    """СЗР: действующее вещество + концентрация + срок ожидания, NPK NULL."""
    seed_inputs(db_session)
    ppps = [r for r in all_inputs(db_session) if r.kind == "ppp"]
    assert ppps
    for row in ppps:
        assert row.active_substance
        assert row.concentration is not None and row.concentration > 0
        assert row.waiting_days is not None and row.waiting_days >= 0
        assert row.n_pct is None
        assert row.p2o5_pct is None
        assert row.k2o_pct is None
        assert row.max_treatments_per_season >= 1  # RULE-AGRO-10


def test_seed_inputs_idempotent(db_session) -> None:
    """Повторный прогон: 0 вставок, дублей нет (ON CONFLICT kind+name)."""
    first = seed_inputs(db_session)
    assert first.inserted >= 30
    second = seed_inputs(db_session)
    assert second.inserted == 0
    assert second.existing == first.inserted
    assert len(all_inputs(db_session)) == first.inserted


def test_seed_inputs_approved_crops(db_session) -> None:
    """Ограниченные СЗР получают UUID культур ref_culture; отсутствующие — ок."""
    make_cultures(db_session)
    report = seed_inputs(db_session)
    rows = {(r.kind, r.name): r for r in all_inputs(db_session)}
    lontrel = rows[("ppp", "Лонтрел 300, ВР")]
    culture_names = set(
        db_session.scalars(select(Culture.name).where(Culture.id.in_(lontrel.approved_crops)))
    )
    assert culture_names <= {"Озимая пшеница", "Яровая пшеница", "Ячмень", "Овёс"}
    assert len(lontrel.approved_crops) == len(culture_names)  # без дублей id
    pivot = rows[("ppp", "Пивот, ВК")]
    assert pivot.approved_crops  # Соя/Горох есть в ref_culture
    unrestricted = rows[("ppp", "Раундап, ВР")]
    assert unrestricted.approved_crops == []  # пусто = без ограничений
    assert report.missing_crops == []
