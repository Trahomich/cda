"""Генератор демо-хозяйства (MVP-13, ADR-07 docs/02-architecture.md §8).

Наполняет БД синтетическим, но валидным демо-хозяйством, чтобы фронтенд и
приёмка сценариев SC-01/03/04 работали без реальных шейпов пользователя:

- 8 полей с контурами 30–150 га вокруг условного центра (52° с.ш., 41° в.д.);
- 6 культурных циклов текущего года (ref_culture) + версии контуров v1;
- 2 плана севооборота (один approved, один draft «на согласовании»);
- 7 операций за прошлый месяц (одна — с deviation_flag, RULE-AGRO-11);
- 3 машины и 4 задания (draft из телематики, confirmed, rejected);
- 2 алерта (weather_danger, ndvi_anomaly).

Идемпотентность: без флага reset повторный запуск ничего не меняет (демо-поля
опознаются по фиксированным именам DEMO_FIELD_NAMES); с reset=True демо-данные
удаляются и создаются заново с новыми UUID. Справочники (ref_culture/ref_source)
и погодные правила актуализируются всегда (идемпотентные сиды MVP-2/MVP-6).

Запуск: python -m app.cli seed-demo [--reset]
"""

import logging
import math
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.agronomy import (
    AgroInput,
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    FieldVersion,
    RotationPlan,
    TreatmentPlan,
    YieldRecord,
)
from app.models.auth import User, UserRole
from app.models.integrations import Forecast, SatelliteScene, WeatherObservation
from app.models.machinery import Machine, MachineJob, Maintenance
from app.models.references import Agrochem, Culture
from app.modules.weather.rules import seed_weather_rules
from app.seed import seed_references

logger = logging.getLogger(__name__)

# Условный центр хозяйства: 52° с.ш., 41° в.д. (Воронежская область)
CENTER_LON = 41.30
CENTER_LAT = 52.05

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_AT_52 = 111_320.0 * math.cos(math.radians(52.0))


@dataclass(frozen=True, slots=True)
class DemoFieldSpec:
    """Спецификация демо-поля: имя, размер и сдвиг от центра хозяйства."""

    name: str
    area_ha: float
    d_lon_m: float  # сдвиг центра к востоку от центра хозяйства, м
    d_lat_m: float  # сдвиг центра к северу
    aspect: float = 1.4  # вытянутость (длина/ширина)
    rotate_deg: float = 0.0  # поворот для разнообразия контуров


# 8 полей 30–150 га, имена — устойчивые (по ним демо-данные опознаются)
DEMO_FIELDS: tuple[DemoFieldSpec, ...] = (
    DemoFieldSpec("Среднее", 96.0, -1500, 800, aspect=1.3, rotate_deg=8),
    DemoFieldSpec("Северное-1", 148.0, 400, 2600, aspect=1.6, rotate_deg=-12),
    DemoFieldSpec("Северное-2", 87.0, 1900, 3100, aspect=1.1, rotate_deg=20),
    DemoFieldSpec("Луговое", 34.0, -3200, -900, aspect=1.8, rotate_deg=-25),
    DemoFieldSpec("Степное-7", 120.0, -800, -2400, aspect=1.4, rotate_deg=5),
    DemoFieldSpec("Южное", 62.0, 1500, -1800, aspect=1.2, rotate_deg=-8),
    DemoFieldSpec("Восточное", 110.0, 3400, 300, aspect=1.5, rotate_deg=15),
    DemoFieldSpec("Заливное", 45.0, -2600, 1600, aspect=1.0, rotate_deg=0),
)

DEMO_FIELD_NAMES = tuple(f.name for f in DEMO_FIELDS)

# Культуры циклов текущего года (имена из ref_culture, сид MVP-2)
DEMO_CULTURES = (
    "Озимая пшеница",
    "Ячмень",
    "Кукуруза",
    "Подсолнечник",
    "Соя",
    "Многолетние травы",
)

DEMO_MACHINES: tuple[dict[str, Any], ...] = (
    {
        "machine_type": "tractor",
        "model": "Кировец К-744Р",
        "gov_number": "А362ВВ36",
        "width_m": 4.2,
        "engine_hours": 4820.5,
    },
    {
        "machine_type": "combine",
        "model": "Torum 760",
        "gov_number": "В118МК36",
        "width_m": 7.0,
        "engine_hours": 2310.0,
    },
    {
        "machine_type": "sprayer",
        "model": "Hardi Navigator",
        "gov_number": "Е907ТК36",
        "width_m": 24.0,
        "tank_capacity_l": 3200,
        "engine_hours": 1560.3,
    },
)


@dataclass(slots=True)
class DemoReport:
    """Итог сида: счётчики созданных сущностей."""

    fields: int = 0
    cycles: int = 0
    plans: int = 0
    operations: int = 0
    machines: int = 0
    jobs: int = 0
    alerts: int = 0
    treatment_plans: int = 0
    inputs: int = 0
    skipped: bool = False
    extra: list[str] = dc_field(default_factory=list)


# Демо-препараты для справочника СЗР/удобрений (RULE-AGRO-07/08/10, задача #46):
# по паре удобрений и СЗР, чтобы экраны E-05/E-16 и проверки доз работали «из коробки».
DEMO_INPUTS: tuple[dict[str, Any], ...] = (
    {
        "kind": "fertilizer",
        "name": "Аммиачная селитра (демо)",
        "n_pct": 34.5,
        "p2o5_pct": 0.0,
        "k2o_pct": 0.0,
        "unit": "kg_ha",
    },
    {
        "kind": "fertilizer",
        "name": "Аммофос (демо)",
        "n_pct": 12.0,
        "p2o5_pct": 52.0,
        "k2o_pct": 0.0,
        "unit": "kg_ha",
    },
    {
        "kind": "ppp",
        "name": "Борус Некст, КЭ (демо)",
        "active_substance": "изопротурон, 500 г/л",
        "concentration": 50.0,
        "unit": "l_ha",
        "waiting_days": 40,
        "max_treatments_per_season": 1,
    },
    {
        "kind": "ppp",
        "name": "Фалькон, КЭ (демо)",
        "active_substance": "спироксамин 250 + тебуконазол 167 + триадименол 43, г/л",
        "concentration": 46.0,
        "unit": "l_ha",
        "waiting_days": 35,
        "max_treatments_per_season": 2,
    },
)


def _seed_users(db: Session, report: DemoReport) -> None:
    """Демо-пользователи для входа в UI: агроном и руководитель (задача #46).

    Идемпотентно: создаётся только отсутствующий логин.
    """
    from app.core.security import hash_password

    demo_users = (
        ("Демо Агроном", "demo_agronomist", UserRole.AGRONOMIST),
        ("Демо Менеджер", "demo_manager", UserRole.MANAGER),
    )
    for fio, login_, role in demo_users:
        if db.scalar(select(User).where(User.login == login_)) is None:
            db.add(
                User(
                    fio=fio,
                    login=login_,
                    password_hash=hash_password("demo123"),
                    role=role,
                    is_active=True,
                )
            )
            db.commit()
            report.extra.append(f"создан демо-пользователь: {login_} / demo123")


def _seed_inputs(db: Session, report: DemoReport) -> None:
    """Демо-препараты в dat_agro_input (задача #46).

    Идемпотентно: ON CONFLICT DO NOTHING по естественному ключу (kind, name).
    """
    stmt = (
        pg_insert(AgroInput)
        .values([dict(spec) for spec in DEMO_INPUTS])
        .on_conflict_do_nothing(index_elements=["kind", "name"])
        .returning(AgroInput.id)
    )
    report.inputs = len(db.execute(stmt).fetchall())
    db.commit()


def _seed_treatment_plans(
    db: Session, report: DemoReport, cycle_by_field: dict[str, CropCycle]
) -> None:
    """Демо-планы внесения: completed + draft + approved (задача #46).

    Ссылаются на демо-циклы текущего года и демо-препараты; удаляются
    каскадно вместе с демо-хозяйством (по cycle_id).
    """
    inputs = {
        (i.kind, i.name): i
        for i in db.scalars(
            select(AgroInput).where(
                AgroInput.name.in_([spec["name"] for spec in DEMO_INPUTS])
            )
        )
    }
    year = date.today().year
    demo_plans = (
        # (поле цикла, препарат, доза, тип, дата, фаза, статус)
        (
            "Северное-1",
            ("fertilizer", "Аммиачная селитра (демо)"),
            90.0,
            "fertilizing",
            date(year, 4, 20),
            "весенняя подкормка",
            "completed",
        ),
        (
            "Среднее",
            ("ppp", "Борус Некст, КЭ (демо)"),
            0.8,
            "spraying",
            None,
            "кущирование (весна следующего сезона)",
            "draft",
        ),
        (
            "Степное-7",
            ("ppp", "Фалькон, КЭ (демо)"),
            0.6,
            "spraying",
            None,
            "обработка по вегетации",
            "approved",
        ),
    )
    for field_name, input_key, dose, op_type, planned_date, phase, status in demo_plans:
        cycle = cycle_by_field.get(field_name)
        input_ = inputs.get(input_key)
        if cycle is None or input_ is None:
            continue
        db.add(
            TreatmentPlan(
                cycle_id=cycle.id,
                input_id=input_.id,
                dose=dose,
                operation_type=op_type,
                planned_date=planned_date,
                planned_phase=phase,
                status=status,
            )
        )
        report.treatment_plans += 1
    db.commit()


def _polygon(spec: DemoFieldSpec) -> dict:
    """Валидный GeoJSON Polygon заданной площади: повёрнутый прямоугольник."""
    area_m2 = spec.area_ha * 10_000.0
    side_w = math.sqrt(area_m2 / spec.aspect)  # ширина, м
    side_l = side_w * spec.aspect  # длина, м
    # локальные координаты углов (м), поворот вокруг центра
    corners = [
        (-side_l / 2, -side_w / 2),
        (side_l / 2, -side_w / 2),
        (side_l / 2, side_w / 2),
        (-side_l / 2, side_w / 2),
    ]
    rad = math.radians(spec.rotate_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    center_lon = CENTER_LON + spec.d_lon_m / M_PER_DEG_LON_AT_52
    center_lat = CENTER_LAT + spec.d_lat_m / M_PER_DEG_LAT
    ring = []
    for x_m, y_m in corners:
        lon = center_lon + (x_m * cos_r - y_m * sin_r) / M_PER_DEG_LON_AT_52
        lat = center_lat + (x_m * sin_r + y_m * cos_r) / M_PER_DEG_LAT
        ring.append([round(lon, 7), round(lat, 7)])
    ring.append(ring[0])  # замкнутое кольцо
    return {"type": "Polygon", "coordinates": [ring]}


def demo_fields_exist(db: Session) -> list[Field]:
    return list(db.scalars(select(Field).where(Field.name.in_(DEMO_FIELD_NAMES))))


def _delete_demo_data(db: Session) -> None:
    """Удалить демо-сущности каскадно вручную (FK без ondelete cascade в MVP)."""
    field_ids = [f.id for f in demo_fields_exist(db)]
    if field_ids:
        demo_cycle_ids = select(CropCycle.id).where(CropCycle.field_id.in_(field_ids))
        demo_op_ids = select(FieldOperation.id).where(FieldOperation.field_id.in_(field_ids))
        # задания техники, ссылающиеся на демо-операции (machine_job.field_operation_id)
        db.execute(delete(MachineJob).where(MachineJob.field_operation_id.in_(demo_op_ids)))
        db.execute(delete(Alert).where(Alert.field_id.in_(field_ids)))
        db.execute(delete(TreatmentPlan).where(TreatmentPlan.cycle_id.in_(demo_cycle_ids)))
        db.execute(delete(YieldRecord).where(YieldRecord.cycle_id.in_(demo_cycle_ids)))
        db.execute(delete(FieldOperation).where(FieldOperation.field_id.in_(field_ids)))
        db.execute(delete(CropCycle).where(CropCycle.field_id.in_(field_ids)))
        db.execute(delete(RotationPlan).where(RotationPlan.field_id.in_(field_ids)))
        db.execute(delete(WeatherObservation).where(WeatherObservation.field_id.in_(field_ids)))
        db.execute(delete(Forecast).where(Forecast.field_id.in_(field_ids)))
        db.execute(delete(SatelliteScene).where(SatelliteScene.field_id.in_(field_ids)))
        db.execute(delete(Agrochem).where(Agrochem.field_id.in_(field_ids)))
        db.execute(delete(FieldVersion).where(FieldVersion.field_id.in_(field_ids)))
        db.execute(delete(Field).where(Field.id.in_(field_ids)))
    # машины и задания демо — по госномерам
    gov_numbers = [m["gov_number"] for m in DEMO_MACHINES]
    demo_machines = list(db.scalars(select(Machine).where(Machine.gov_number.in_(gov_numbers))))
    if demo_machines:
        machine_ids = [m.id for m in demo_machines]
        db.execute(delete(Maintenance).where(Maintenance.machine_id.in_(machine_ids)))
        db.execute(delete(MachineJob).where(MachineJob.machine_id.in_(machine_ids)))
        db.execute(delete(Machine).where(Machine.id.in_(machine_ids)))
    db.commit()


def seed_demo(db: Session, *, reset: bool = False) -> DemoReport:
    """Наполнить БД демо-хозяйством (идемпотентно; reset пересоздаёт)."""
    report = DemoReport()

    # базовые справочники и правила — всегда актуальны (идемпотентны)
    seed_references(db)
    seed_weather_rules(db)

    existing = demo_fields_exist(db)
    if existing and not reset:
        report.skipped = True
        report.extra.append(
            f"демо-хозяйство уже есть ({len(existing)} полей); перезапуск с --reset пересоздаст его"
        )
        return report
    if existing and reset:
        _delete_demo_data(db)

    year = date.today().year
    now = datetime.now(tz=UTC)

    # --- поля + версия контура v1 ------------------------------------------
    fields: dict[str, Field] = {}
    for spec in DEMO_FIELDS:
        polygon = _polygon(spec)
        ring = ", ".join(f"{lon} {lat}" for lon, lat in polygon["coordinates"][0])
        contour = f"SRID=4326;POLYGON(({ring}))"
        demo_field = Field(name=spec.name, contour=contour, status="active")
        db.add(demo_field)
        db.flush()
        version = FieldVersion(
            field_id=demo_field.id,
            version_no=1,
            contour=contour,
            valid_from=now.date() - timedelta(days=365),
            reason="демо-хозяйство (MVP-13)",
        )
        db.add(version)
        db.flush()
        demo_field.current_version_id = version.id
        fields[spec.name] = demo_field
        report.fields += 1
    db.commit()

    # --- культурные циклы текущего года ------------------------------------
    cultures = {c.name: c for c in db.scalars(select(Culture))}
    cycle_by_field: dict[str, CropCycle] = {}
    planned_yields = {
        "Озимая пшеница": 55.0,
        "Ячмень": 45.0,
        "Кукуруза": 70.0,
        "Подсолнечник": 28.0,
        "Соя": 25.0,
        "Многолетние травы": 40.0,
    }
    harvest_dates = {
        "Озимая пшеница": date(year, 7, 20),
        "Ячмень": date(year, 7, 10),
        "Кукуруза": date(year, 9, 25),
        "Подсолнечник": date(year, 9, 15),
        "Соя": date(year, 9, 10),
        "Многолетние травы": date(year, 6, 15),
    }
    for spec, culture_name in zip(DEMO_FIELDS, DEMO_CULTURES, strict=False):
        culture = cultures[culture_name]
        cycle = CropCycle(
            field_id=fields[spec.name].id,
            year=year,
            culture_id=culture.id,
            kind="main",
            planned_yield_c_ha=planned_yields[culture_name],
            planned_harvest_date=harvest_dates[culture_name],
            status="in_progress",
        )
        db.add(cycle)
        cycle_by_field[spec.name] = cycle
        report.cycles += 1
    db.commit()

    # --- планы севооборота: один approved, один draft «на согласовании» ----
    db.add(
        RotationPlan(
            field_id=fields["Северное-1"].id,
            year_from=year,
            year_to=year + 4,
            status="approved",
            approved_by="Руководитель КФХ «Восход»",
            approved_at=now - timedelta(days=12),
        )
    )
    db.add(
        RotationPlan(
            field_id=fields["Степное-7"].id,
            year_from=year + 1,
            year_to=year + 5,
            status="draft",
        )
    )
    report.plans += 2
    db.commit()

    # --- операции за прошлый месяц (одна с отклонением, RULE-AGRO-11) ------
    month_ago = date.today() - timedelta(days=30)
    operations = (
        # (поле, тип, смещение суток, исполнитель, отклонение?)
        ("Среднее", "fertilizing", 2, "Иванов И.", False),
        ("Северное-1", "seeding", 5, "Петров П.", False),
        ("Северное-2", "spraying", 8, "Сидоров С.", False),
        ("Луговое", "tillage", 11, "Иванов И.", False),
        ("Степное-7", "fertilizing", 14, "Кузнецов К.", True),  # отклонение от плана
        ("Южное", "spraying", 18, "Петров П.", False),
        ("Восточное", "tillage", 22, "Смирнов А.", False),  # поле в паре, цикла нет
    )
    for name, op_type, day_off, performer, deviation in operations:
        op_spec = next(f for f in DEMO_FIELDS if f.name == name)
        db.add(
            FieldOperation(
                field_id=fields[name].id,
                cycle_id=cy.id if (cy := cycle_by_field.get(name)) is not None else None,
                operation_type=op_type,
                date=month_ago + timedelta(days=day_off),
                actual_area_ga=op_spec.area_ha
                if not deviation
                else round(op_spec.area_ha * 0.93, 2),
                performer=performer,
                source="manual",
                deviation_flag=deviation,
                deviation_rationale=(
                    "недосев 7 % площади: дождь прервал работу (демо, RULE-AGRO-11)"
                    if deviation
                    else None
                ),
                status="done",
            )
        )
        report.operations += 1
    db.commit()

    # --- парк техники и задания -------------------------------------------
    machines: list[Machine] = []
    for m_spec in DEMO_MACHINES:
        machine = Machine(**m_spec, status="active")
        db.add(machine)
        machines.append(machine)
        report.machines += 1
    db.flush()
    jobs = (
        # (машина, поле-операция, сутки назад, га, статус, источник)
        (0, "Среднее", 3, 96.0, "confirmed", "manual"),
        (1, "Восточное", 24, 110.0, "confirmed", "telemetry"),
        (2, "Северное-2", 9, 87.0, "draft", "telemetry"),  # черновик из телематики
        (0, "Степное-7", 16, 111.6, "rejected", "telemetry"),
    )
    for machine_idx, _field_name, days_ago, area_ha, status, source in jobs:
        db.add(
            MachineJob(
                machine_id=machines[machine_idx].id,
                performed_at=date.today() - timedelta(days=days_ago),
                area_ha=area_ha,
                fuel_l=round(area_ha * 9.5, 1),
                duration_h=round(area_ha / 11.0, 2),
                status=status,
                source=source,
            )
        )
        report.jobs += 1
    db.commit()

    # --- демо-погода: факты 14 сут + прогноз 7 сут (напрямую в БД, MVP-6) ---
    import random

    rng = random.Random(20261105)  # стабильные демо-данные
    source_id = "weather.open-meteo"
    from app.models.integrations import Forecast, WeatherObservation

    today = date.today()
    for demo_field in fields.values():
        for day_off in range(-14, 0):  # факты: последние 14 суток
            t_min = rng.uniform(4.0, 12.0)
            t_max = t_min + rng.uniform(7.0, 13.0)
            db.add(
                WeatherObservation(
                    field_id=demo_field.id,
                    observed_at=today + timedelta(days=day_off),
                    temperature_avg_c=round((t_min + t_max) / 2, 1),
                    temperature_min_c=round(t_min, 1),
                    temperature_max_c=round(t_max, 1),
                    precipitation_mm=round(rng.choice([0.0, 0.0, 0.0, 0.5, 3.2, 11.0]), 1),
                    wind_max_ms=round(rng.uniform(2.0, 9.0), 1),
                    humidity_avg_pct=round(rng.uniform(45.0, 85.0), 1),
                    source_id=source_id,
                )
            )
        for day_off in range(0, 7):  # прогноз: 7 суток, issued_at = сейчас
            t_min = rng.uniform(5.0, 13.0)
            t_max = t_min + rng.uniform(7.0, 12.0)
            db.add(
                Forecast(
                    field_id=demo_field.id,
                    forecast_for=today + timedelta(days=day_off),
                    issued_at=now,
                    temperature_min_c=round(t_min, 1),
                    temperature_max_c=round(t_max, 1),
                    precipitation_mm=round(rng.choice([0.0, 0.0, 0.2, 1.5, 6.0]), 1),
                    wind_max_ms=round(rng.uniform(2.0, 11.0), 1),
                    humidity_avg_pct=round(rng.uniform(45.0, 80.0), 1),
                    hazard="none",
                )
            )
    db.commit()

    # --- алерты -------------------------------------------------------------
    db.add(
        Alert(
            type="weather_danger",
            severity="warning",
            field_id=fields["Среднее"].id,
            payload={
                "text": "Заморозки до −2 °C в ночь на 12 мая: риск для всходов (демо)",
                "hazard": "frost",
            },
            status="new",
        )
    )
    db.add(
        Alert(
            type="ndvi_anomaly",
            severity="warning",
            field_id=fields["Северное-2"].id,
            payload={
                "text": "Падение NDVI на 0.18 за 10 суток в восточной части поля (демо)",
                "ndvi_delta": -0.18,
            },
            status="new",
        )
    )
    report.alerts += 2
    db.commit()

    # демо-агроном для входа в UI (если ещё нет)
    if db.scalar(select(User).where(User.login == "demo_agronomist")) is None:
        from app.core.security import hash_password

        db.add(
            User(
                fio="Демо Агроном",
                login="demo_agronomist",
                password_hash=hash_password("demo123"),
                role=UserRole.AGRONOMIST,
                is_active=True,
            )
        )
        db.commit()
        report.extra.append("создан демо-агроном: demo_agronomist / demo123")

    return report
