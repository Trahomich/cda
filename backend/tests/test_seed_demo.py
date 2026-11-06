"""Тесты генератора демо-хозяйства (MVP-13, задача #31).

Проверяется контракт seed_demo (app/seed_demo.py):
- сид создаёт согласованный набор сущностей (8 полей, 6 циклов, 2 плана,
  7 операций с одним отклонением, 3 машины, 4 задания, 2 алерта,
  демо-погода и демо-агроном);
- контуры полей — валидные полигоны PostGIS заданной площади (30–150 га);
- идемпотентность: повторный сид без reset ничего не меняет;
- reset=True пересоздаёт демо-данные с новыми идентификаторами.
"""

from datetime import date

from app.models.agronomy import (
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    FieldVersion,
    RotationPlan,
)
from app.models.auth import User, UserRole
from app.models.integrations import Forecast, WeatherObservation
from app.models.machinery import Machine, MachineJob
from app.models.references import Culture
from app.seed_demo import DEMO_FIELDS, seed_demo
from sqlalchemy import func, select, text


def _counts(db):
    """Счётчики демо-сущностей одним словарём — для сравнения до/после."""
    return {
        "fields": db.scalar(select(func.count(Field.id))),
        "versions": db.scalar(select(func.count(FieldVersion.id))),
        "cycles": db.scalar(select(func.count(CropCycle.id))),
        "plans": db.scalar(select(func.count(RotationPlan.id))),
        "operations": db.scalar(select(func.count(FieldOperation.id))),
        "machines": db.scalar(select(func.count(Machine.id))),
        "jobs": db.scalar(select(func.count(MachineJob.id))),
        "alerts": db.scalar(select(func.count(Alert.id))),
        "observations": db.scalar(select(func.count(WeatherObservation.id))),
        "forecasts": db.scalar(select(func.count(Forecast.id))),
    }


def test_seed_creates_demo_farm(db_session):
    report = seed_demo(db_session)

    assert report.skipped is False
    assert report.fields == len(DEMO_FIELDS) == 8
    assert report.cycles == 6
    assert report.plans == 2
    assert report.operations == 7
    assert report.machines == 3
    assert report.jobs == 4
    assert report.alerts == 2

    # поля: все активные, с версией контура v1 и посчитанной площадью
    fields = db_session.scalars(select(Field)).all()
    assert {f.name for f in fields} == {s.name for s in DEMO_FIELDS}
    for f in fields:
        assert f.status == "active"
        assert f.current_version_id is not None
        assert f.area_ga is not None
        assert 25 < float(f.area_ga) < 155  # га из контура, с допуском
    versions = db_session.scalars(select(FieldVersion)).all()
    assert all(v.version_no == 1 for v in versions)

    # циклы текущего года: kind=main, статус in_progress, план урожайности задан
    year = date.today().year
    cycles = db_session.scalars(select(CropCycle)).all()
    assert len(cycles) == 6
    assert all(c.year == year and c.kind == "main" and c.status == "in_progress" for c in cycles)
    assert all(
        c.planned_yield_c_ha is not None and c.planned_harvest_date is not None for c in cycles
    )

    # планы севооборота: один approved, один draft
    plan_statuses = set(db_session.scalars(select(RotationPlan.status)))
    assert plan_statuses == {"approved", "draft"}

    # операции за прошлый месяц: одна с deviation_flag и обоснованием
    ops = db_session.scalars(select(FieldOperation)).all()
    deviations = [op for op in ops if op.deviation_flag]
    assert len(deviations) == 1
    assert deviations[0].deviation_rationale is not None
    assert all(op.status == "done" for op in ops)

    # техника и задания
    assert db_session.scalar(select(func.count(Machine.id))) == 3
    jobs = db_session.scalars(select(MachineJob)).all()
    assert len(jobs) == 4
    assert {j.status for j in jobs} == {"confirmed", "draft", "rejected"}

    # алерты: weather_danger и ndvi_anomaly, новые
    alerts = db_session.scalars(select(Alert)).all()
    assert {a.type for a in alerts} == {"weather_danger", "ndvi_anomaly"}
    assert all(a.status == "new" for a in alerts)

    # демо-погода: 14 суток фактов и 7 суток прогноза на каждое поле
    assert db_session.scalar(select(func.count(WeatherObservation.id))) == 8 * 14
    assert db_session.scalar(select(func.count(Forecast.id))) == 8 * 7

    # демо-агроном и демо-руководитель для входа в UI (задача #46)
    demo_user = db_session.scalar(select(User).where(User.login == "demo_agronomist"))
    assert demo_user is not None
    assert demo_user.role == UserRole.AGRONOMIST
    assert demo_user.is_active is True
    demo_manager = db_session.scalar(select(User).where(User.login == "demo_manager"))
    assert demo_manager is not None
    assert demo_manager.role == UserRole.MANAGER
    assert demo_manager.is_active is True

    # демо-препараты и планы внесения (задача #46)
    from app.models.agronomy import AgroInput, TreatmentPlan as TreatmentPlanModel
    from app.models.references import PredecessorRule

    inputs = db_session.scalars(select(AgroInput)).all()
    assert len(inputs) == 4
    assert {i.kind for i in inputs} == {"fertilizer", "ppp"}
    assert all(i.waiting_days is not None for i in inputs if i.kind == "ppp")
    treatment_plans = db_session.scalars(select(TreatmentPlanModel)).all()
    assert len(treatment_plans) == 3
    assert {p.status for p in treatment_plans} == {"completed", "draft", "approved"}

    # матрица предшественников: минимум по одной warn- и ban-паре (RULE-AGRO-01)
    rule_verdicts = set(db_session.scalars(select(PredecessorRule.verdict)))
    assert {"ok", "warn", "ban"} <= rule_verdicts

    # справочники и погодные правила актуализированы
    assert db_session.scalar(select(func.count(Culture.id))) >= 6


def test_seed_contours_valid_postgis(db_session):
    """Контуры — валидные полигоны заданной площади (проверка на PostGIS)."""
    seed_demo(db_session)

    rows = db_session.execute(
        text(
            "SELECT name, ST_IsValid(contour::geometry) AS valid, "
            "ST_Area(contour::geography) AS area_m2 FROM dat_field"
        )
    ).all()
    assert len(rows) == 8
    by_name = {r.name: (r.valid, float(r.area_m2)) for r in rows}
    for spec in DEMO_FIELDS:
        valid, area_m2 = by_name[spec.name]
        assert valid, f"контур {spec.name} невалиден"
        assert abs(area_m2 - spec.area_ha * 10_000) <= spec.area_ha * 10_000 * 0.02, (
            f"{spec.name}: площадь {area_m2 / 10_000:.1f} га вместо {spec.area_ha} га"
        )


def test_seed_idempotent_without_reset(db_session):
    first = seed_demo(db_session)
    assert first.skipped is False

    ids_before = set(db_session.scalars(select(Field.id)))
    counts_before = _counts(db_session)

    second = seed_demo(db_session)
    assert second.skipped is True
    assert second.fields == 0

    # ничего не изменилось и не продублировалось
    assert set(db_session.scalars(select(Field.id))) == ids_before
    assert _counts(db_session) == counts_before


def test_seed_reset_recreates_demo_data(db_session):
    seed_demo(db_session)
    ids_before = set(db_session.scalars(select(Field.id)))
    counts_before = _counts(db_session)
    assert len(ids_before) == 8

    second = seed_demo(db_session, reset=True)
    assert second.skipped is False
    assert second.fields == 8

    # пересоздано: другие идентификаторы, те же счётчики
    ids_after = set(db_session.scalars(select(Field.id)))
    assert len(ids_after) == 8
    assert ids_after.isdisjoint(ids_before)
    assert _counts(db_session) == counts_before

    # отклонение по-прежнему одно (RULE-AGRO-11 не теряется при reset)
    deviations = db_session.scalars(
        select(FieldOperation).where(FieldOperation.deviation_flag)
    ).all()
    assert len(deviations) == 1


def test_seed_demo_cli_registered():
    """Подкоманда seed-demo доступна в CLI (make seed её вызывает)."""
    # argparse: --reset есть, дефолтного поведения не трогаем (БД занята фикстурой)
    import contextlib
    import io

    from app.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            main(["seed-demo", "--help"])
        except SystemExit as exc:
            assert exc.code == 0
    assert "--reset" in buf.getvalue()
