"""Тесты сида истории работ и планов внесения (v2-02, #72) — docs/06 §7.2.

seed_operations: по каждому активному полю с циклами — история операций
(2022–2025, done) и планы 2026 (draft+approved); детерминированность (два
прогона — одинаковые наборы), идемпотентность (естественные ключи
(field,date,type) / (cycle,input,planned_date)), отсутствие 422-нарушений
(RULE-AGRO-07/08/10 через validate_treatment_plan), операции в границах
календарного года цикла.
"""

from datetime import date

import pytest
from app.models.agronomy import AgroInput, CropCycle, Field, FieldOperation, TreatmentPlan
from app.seed import seed_references
from app.seed_inputs import seed_inputs
from app.seed_operations import seed_operations
from app.seed_rotation import seed_rotation
from sqlalchemy import delete, select
from tests.test_seed_rotation import make_field

CURRENT_YEAR = date.today().year


def _prepare(db_session, *, n_fields: int = 6) -> list[Field]:
    """Справочники, препараты, поля и циклы (как после полного v1/v2-01 сида)."""
    seed_references(db_session)
    seed_inputs(db_session)
    fields = [
        make_field(db_session, name=f"Поле-{i:02d}", external_id=f"EXT-{i:03d}")
        for i in range(n_fields)
    ]
    db_session.commit()
    seed_rotation(db_session, years_back=4, years_forward=4)
    return fields


def _operation_facts(db_session) -> list[tuple]:
    rows = db_session.execute(
        select(
            FieldOperation.field_id,
            FieldOperation.date,
            FieldOperation.operation_type,
            FieldOperation.input_id,
            FieldOperation.actual_dose,
            FieldOperation.performer,
            FieldOperation.cycle_id,
        )
    ).fetchall()
    return sorted(
        (str(f), d, t, str(i), float(dos or 0), p, str(c))
        for f, d, t, i, dos, p, c in rows
    )


def _plan_facts(db_session) -> list[tuple]:
    rows = db_session.execute(
        select(
            TreatmentPlan.cycle_id,
            TreatmentPlan.input_id,
            TreatmentPlan.planned_date,
            TreatmentPlan.dose,
            TreatmentPlan.status,
            TreatmentPlan.operation_type,
        )
    ).fetchall()
    return sorted((str(c), str(i), d, float(dos), s, t) for c, i, d, dos, s, t in rows)


def test_seed_operations_creates_history_and_plans(db_session) -> None:
    """История по прошлым годам + планы draft+approved на сезон-план."""
    _prepare(db_session)
    report = seed_operations(db_session)

    ops = db_session.scalars(select(FieldOperation)).all()
    plans = db_session.scalars(select(TreatmentPlan)).all()
    assert report.fields_processed > 0
    assert len(ops) >= report.fields_processed * 4  # ≥ tillage+посев+внесение+уборка
    assert ops
    for op in ops:
        assert op.status == "done"
        assert op.source == "manual"
        assert op.actual_area_ga > 0
        assert op.performer
    # планы: есть и approved, и draft; только fertilizing/spraying/seed_treatment
    statuses = {p.status for p in plans}
    assert statuses <= {"approved", "draft"}
    assert "approved" in statuses and "draft" in statuses
    for plan in plans:
        assert plan.operation_type in ("fertilizing", "spraying", "seed_treatment")
        assert plan.planned_date is not None
    #approved ≈70 %: мягкая проверка на маленькой выборке
    assert any(p.status == "approved" for p in plans)


def test_seed_operations_operations_within_cycles(db_session) -> None:
    """Операции в календарном году своего цикла, cycle_id корректен."""
    _prepare(db_session)
    seed_operations(db_session)
    cycles = {c.id: c for c in db_session.scalars(select(CropCycle)).all()}
    for op in db_session.scalars(select(FieldOperation)).all():
        assert op.cycle_id in cycles
        assert cycles[op.cycle_id].year == op.date.year


def test_seed_operations_no_rule_violations(db_session) -> None:
    """Сид не создаёт планов, которые API отверг бы (RULE-AGRO-07/08/10)."""
    _prepare(db_session)
    report = seed_operations(db_session)
    assert report.violations == []
    assert report.plans_created > 0


def test_seed_operations_idempotent_and_deterministic(db_session) -> None:
    """Повторный прогон: 0 новых; после очистки — идентичный набор."""
    _prepare(db_session)
    first = seed_operations(db_session)
    assert first.operations_created > 0 and first.plans_created > 0

    second = seed_operations(db_session)
    assert second.operations_created == 0
    assert second.plans_created == 0
    assert second.operations_skipped == first.operations_created
    assert second.plans_skipped == first.plans_created

    ops_before, plans_before = _operation_facts(db_session), _plan_facts(db_session)
    db_session.execute(delete(FieldOperation))
    db_session.execute(delete(TreatmentPlan))
    db_session.commit()
    third = seed_operations(db_session)
    assert third.operations_created == first.operations_created
    assert third.plans_created == first.plans_created
    assert _operation_facts(db_session) == ops_before
    assert _plan_facts(db_session) == plans_before


def test_seed_operations_requires_inputs(db_session) -> None:
    """Без справочника препаратов — понятная ошибка (сначала seed-inputs)."""
    seed_references(db_session)
    make_field(db_session, name="Пустое", external_id="EXT-X")
    db_session.commit()
    seed_rotation(db_session, years_back=1, years_forward=1)
    with pytest.raises(ValueError, match="seed-inputs"):
        seed_operations(db_session)


def test_seed_operations_spraying_respects_approved_crops(db_session) -> None:
    """Планы spraying только с препаратами, разрешёнными культуре (RULE-AGRO-10)."""
    from app.models.references import Culture

    _prepare(db_session)
    seed_operations(db_session)
    {c.id: c for c in db_session.scalars(select(Culture)).all()}
    inputs = {i.id: i for i in db_session.scalars(select(AgroInput)).all()}
    for plan in db_session.scalars(select(TreatmentPlan)).all():
        if plan.operation_type != "spraying":
            continue
        item = inputs[plan.input_id]
        cycle = db_session.get(CropCycle, plan.cycle_id)
        crops = list(item.approved_crops or [])
        assert not crops or cycle.culture_id in crops


def test_seed_operations_unique_natural_keys(db_session) -> None:
    """Нет дублей по естественным ключам (field,date,type) и (cycle,input,date)."""
    _prepare(db_session)
    seed_operations(db_session)
    op_keys = db_session.execute(
        select(FieldOperation.field_id, FieldOperation.date, FieldOperation.operation_type)
    ).fetchall()
    assert len(op_keys) == len(set(op_keys))
    plan_keys = db_session.execute(
        select(TreatmentPlan.cycle_id, TreatmentPlan.input_id, TreatmentPlan.planned_date)
    ).fetchall()
    assert len(plan_keys) == len(set(plan_keys))
