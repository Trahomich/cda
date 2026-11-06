"""История работ и планы внесения на реальном хозяйстве (v2-02, #72).

CLI ``python -m app.cli seed-operations``: по каждому активному полю с циклами
(seed-rotation уже создал их из реального контура) — осмысленная история
операций за прошлые сезоны и планы внесения на сезон-план 2026 (docs/06
§7.2, сущности docs/03 §1.5–1.6).

История (годы current−4 … current−1, все status='done', source='manual'):
tillage → посев → 1–3 внесения удобрений → 0–2 обработки СЗР → уборка;
пар — только обработки почвы. Планы 2026 (main-цикл, не пар): 1–3 плана
(fertilizing / spraying / seed_treatment), ~70 % approved / 30 % draft,
каждый прогоняет те же проверки, что и POST /treatment-plans
(``validate_treatment_plan``, RULE-AGRO-07/08/10): сид не создаёт планов,
которые API отверг бы 422.

Детерминированность: RNG от ``Random(f"ops:{external_id|id}:{год}")`` и
``Random(f"plans:{external_id|id}:{год}")`` — на одних и тех же данных прогоны
дают одинаковые наборы операций и планов.

Идемпотентность (естественные ключи, docstring-фиксация #72):
- операция — (field_id, date, operation_type): в один день на поле одна
  операция каждого типа; повторный прогон пропускает существующие;
- план — (cycle_id, input_id, planned_date): тот же препарат на ту же дату
  в цикле не дублируется.

Допущения: реальные журналы работ хозяйство не передавало (как в seed-rotation
#66) — даты/дозы правдоподобны и воспроизводимы; посев озимых отнесён к
календарному году цикла (урожай условно того же года); machine_job_id не
заполняется (техника вне итерации v2).
"""

import logging
import random
import uuid
from dataclasses import asdict, dataclass, field as dc_field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.agronomy import AgroInput, CropCycle, Field, FieldOperation, TreatmentPlan
from app.models.references import Culture
from app.modules.inputs.service import validate_treatment_plan
from app.seed_rotation import _stable_key

logger = logging.getLogger(__name__)

HISTORY_YEARS_BACK = 4  # 2026 → история 2022–2025
PLAN_YEAR_OFFSET = 0  # планы — на текущий год (2026)

# исполнители журнала работ (механизаторы/агрономы хозяйства)
PERFORMERS: tuple[str, ...] = (
    "Иванов И. И.",
    "Петров П. С.",
    "Сидоров С. В.",
    "Кузнецов К. А.",
    "Смирнов А. Н.",
    "Гаврилов Г. М.",
)

# удобрения истории: имя препарата (из seed-inputs) → диапазон дозы, кг/га
FERT_HISTORY: tuple[tuple[str, float, float], ...] = (
    ("Аммиачная селитра", 90.0, 180.0),
    ("Мочевина (карбамид)", 60.0, 120.0),
    ("NPK 16:16:16 (нитроаммофоска)", 100.0, 220.0),
    ("Аммофос", 80.0, 150.0),
)
# обработки истории по группам культур: группа → препараты (л/га)
SPRAY_HISTORY_BY_GROUP: dict[str, tuple[tuple[str, float, float], ...]] = {
    "cereals": (
        ("Гранстар Про, ВДГ", 0.02, 0.03),
        ("Скор, КЭ", 0.4, 0.6),
        ("Каратэ Зеон, МКС", 0.15, 0.25),
        ("БИ-58 Новый, КЭ", 0.8, 1.2),
    ),
    "legumes": (
        ("Зеллек Супер, КЭ", 0.8, 1.2),
        ("Фундазол, СП", 0.6, 1.0),
        ("Абига-Пик, ВС", 2.5, 3.5),
    ),
    "oilseeds": (
        ("Зеллек Супер, КЭ", 0.8, 1.2),
        ("Топаз, КЭ", 0.4, 0.6),
        ("Фуфанон Нова, КЭ", 0.8, 1.2),
    ),
}
# прочие группы (tubers/root_crops/vegetables/forage/пусто) — общий пул
SPRAY_HISTORY_DEFAULT: tuple[tuple[str, float, float], ...] = (
    ("Базагран, ВР", 2.0, 3.0),
    ("Абига-Пик, ВС", 2.5, 3.5),
    ("Децис Профи, ВДГ", 0.05, 0.1),
)

# планы 2026: шаблоны (тип, препараты-кандидаты, диапазон дозы, диапазон дат)
PLAN_TEMPLATES: tuple[dict, ...] = (
    {"op": "fertilizing", "names": ("Мочевина (карбамид)", "Аммиачная селитра"),
     "dose": (60.0, 110.0), "days": (4, 5), "phase": "весенняя подкормка"},
    {"op": "spraying", "names": ("Гранстар Про, ВДГ", "Топаз, КЭ", "Абига-Пик, ВС",
                                 "Каратэ Зеон, МКС", "Базагран, ВР"),
     "dose": (0.4, 1.2), "days": (5, 6), "phase": "обработка по вегетации"},
    {"op": "seed_treatment", "names": ("Максим, КС", "ТМТД, ВСК"),
     "dose": (1.0, 2.0), "days": (3, 4), "phase": "протравливание семян"},
)
APPROVED_SHARE = 0.7


@dataclass
class SeedOperationsReport:
    """Итог сида: что создано/пропущено, были ли нарушения правил."""

    fields_processed: int = 0
    years_from: int = 0
    years_to: int = 0
    plan_year: int = 0
    operations_created: int = 0
    operations_skipped: int = 0
    plans_created: int = 0
    plans_approved: int = 0
    plans_draft: int = 0
    plans_skipped: int = 0
    violations: list[str] = dc_field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _may_spray_on(item: AgroInput, culture: Culture) -> bool:
    """RULE-AGRO-10 для планов: препарат разрешён культуре (пусто = без ограничений)."""
    crops = list(item.approved_crops or [])
    return not crops or culture.id in crops


def _history_rows(
    rng: random.Random, *, field: Field, cycle: CropCycle, culture: Culture, area_ga: float
) -> list[dict]:
    """Операции одного сезона: по группе культуры и признаку пара."""
    year = cycle.year
    fallow = cycle.kind == "fallow"
    rows: list[dict] = [
        {"operation_type": "tillage",
         "date": date(year, 4, rng.randint(3, 20)),
         "input_id": None, "actual_dose": None},
    ]
    if fallow:
        rows.append({"operation_type": "tillage",
                     "date": date(year, 6, rng.randint(5, 25)),
                     "input_id": None, "actual_dose": None})
        rows.append({"operation_type": "tillage",
                     "date": date(year, 8, rng.randint(5, 25)),
                     "input_id": None, "actual_dose": None})
        return rows

    winter = culture.name.startswith("Озимая")
    # посев: озимые — сентябрь, яровые — конец апреля/май (допущение docstring)
    rows.append({"operation_type": "seeding",
                 "date": (date(year, 9, rng.randint(5, 25)) if winter
                          else date(year, 4, rng.randint(20, 30))),
                 "input_id": None, "actual_dose": None})
    # внесения: 1–3, апрель–июнь
    for _ in range(rng.randint(1, 3)):
        name, lo, hi = FERT_HISTORY[rng.randrange(len(FERT_HISTORY))]
        month = rng.randint(4, 6)
        rows.append({"operation_type": "fertilizing",
                     "date": date(year, month, rng.randint(2, 27)),
                     "input_name": name,
                     "actual_dose": round(rng.uniform(lo, hi), 2)})
    # обработки: 0–2, май–июль
    spray_pool = SPRAY_HISTORY_BY_GROUP.get(
        culture.crop_group or "", SPRAY_HISTORY_DEFAULT
    )
    for _ in range(rng.randint(0, 2)):
        name, lo, hi = spray_pool[rng.randrange(len(spray_pool))]
        month = rng.randint(5, 7)
        rows.append({"operation_type": "spraying",
                     "date": date(year, month, rng.randint(2, 27)),
                     "input_name": name,
                     "actual_dose": round(rng.uniform(lo, hi), 2)})
    # уборка: июль–сентябрь (масличные/бобовые — август–октябрь)
    last_month = 10 if (culture.crop_group in ("oilseeds", "legumes", "tubers")) else 9
    month = rng.randint(7, last_month)
    rows.append({"operation_type": "harvesting",
                 "date": date(year, month, rng.randint(5, 27)),
                 "input_id": None, "actual_dose": None})
    # без дубля (field, date, operation_type): добиваем даты уникальностью типа
    seen: set[tuple[date, str]] = set()
    for row in rows:
        while (row["date"], row["operation_type"]) in seen:
            row["date"] += timedelta(days=1)
        seen.add((row["date"], row["operation_type"]))
    for row in rows:
        row["actual_area_ga"] = round(float(area_ga) * rng.uniform(0.97, 1.03), 2)
        row["performer"] = PERFORMERS[rng.randrange(len(PERFORMERS))]
    return rows


def seed_operations(db: Session) -> SeedOperationsReport:
    """Создать историю работ и планы внесения по активным полям с циклами."""
    current_year = date.today().year
    report = SeedOperationsReport(
        years_from=current_year - HISTORY_YEARS_BACK,
        years_to=current_year - 1,
        plan_year=current_year + PLAN_YEAR_OFFSET,
    )

    inputs_by_name: dict[tuple[str, str], AgroInput] = {
        (i.kind, i.name): i for i in db.scalars(select(AgroInput)).all()
    }
    if not inputs_by_name:
        raise ValueError("dat_agro_input пуст — сначала seed-inputs (v2-01)")

    fields = list(db.scalars(select(Field).where(Field.status == "active")).all())
    cycles = list(
        db.scalars(
            select(CropCycle).where(
                CropCycle.field_id.in_([f.id for f in fields]),
                CropCycle.year.between(report.years_from, report.plan_year),
            )
        ).all()
    )
    cultures = {c.id: c for c in db.scalars(select(Culture)).all()}

    # естественные ключи существующих строк (идемпотентность, docstring модуля)
    op_keys = set(
        db.execute(
            select(FieldOperation.field_id, FieldOperation.date, FieldOperation.operation_type)
        ).fetchall()
    )
    plan_keys = set(
        db.execute(
            select(TreatmentPlan.cycle_id, TreatmentPlan.input_id, TreatmentPlan.planned_date)
        ).fetchall()
    )

    cycles_by_field: dict[uuid.UUID, list[CropCycle]] = {}
    for cycle in cycles:
        cycles_by_field.setdefault(cycle.field_id, []).append(cycle)

    for farm_field in fields:
        field_cycles = sorted(cycles_by_field.get(farm_field.id, []), key=lambda c: c.year)
        if not field_cycles:
            continue
        report.fields_processed += 1
        area_ga = float(farm_field.area_ga or 0) or 1.0

        # --- история прошлых сезонов -----------------------------------------
        for cycle in (c for c in field_cycles if c.year <= report.years_to):
            culture = cultures[cycle.culture_id]
            rng = random.Random(f"ops:{_stable_key(farm_field)}:{cycle.year}")
            for row in _history_rows(
                rng, field=farm_field, cycle=cycle, culture=culture, area_ga=area_ga
            ):
                key = (farm_field.id, row["date"], row["operation_type"])
                if key in op_keys:
                    report.operations_skipped += 1
                    continue
                input_id = None
                if "input_name" in row:
                    kind = "fertilizer" if row["operation_type"] == "fertilizing" else "ppp"
                    item = inputs_by_name.get((kind, row["input_name"]))
                    if item is None:  # справочник неполный — операция без препарата
                        logger.warning(
                            "seed-operations: препарата «%s» нет в dat_agro_input", row["input_name"]
                        )
                    input_id = item.id if item is not None else None
                op = FieldOperation(
                    field_id=farm_field.id,
                    cycle_id=cycle.id,
                    operation_type=row["operation_type"],
                    date=row["date"],
                    input_id=input_id,
                    actual_dose=row["actual_dose"],
                    actual_area_ga=row["actual_area_ga"],
                    performer=row["performer"],
                    source="manual",
                    deviation_flag=False,
                    status="done",
                )
                db.add(op)
                op_keys.add(key)
                report.operations_created += 1

        # --- планы сезона-плана ----------------------------------------------
        plan_cycle = next(
            (c for c in field_cycles if c.year == report.plan_year and c.kind == "main"), None
        )
        if plan_cycle is None:
            continue
        culture = cultures[plan_cycle.culture_id]
        rng = random.Random(f"plans:{_stable_key(farm_field)}:{report.plan_year}")
        templates = list(PLAN_TEMPLATES)
        rng.shuffle(templates)
        for tpl in templates[: rng.randint(1, len(PLAN_TEMPLATES))]:
            candidates = [
                inputs_by_name[("fertilizer" if tpl["op"] == "fertilizing" else "ppp", name)]
                for name in tpl["names"]
                if ("fertilizer" if tpl["op"] == "fertilizing" else "ppp", name)
                in inputs_by_name
            ]
            candidates = [
                i for i in candidates
                if tpl["op"] != "spraying" or _may_spray_on(i, culture)
            ]
            if not candidates:
                continue
            item = candidates[rng.randrange(len(candidates))]
            month, day = tpl["days"]
            planned = date(
                report.plan_year, month, max(1, min(28, rng.randint(day, min(27, day + 14))))
            )
            key = (plan_cycle.id, item.id, planned)
            if key in plan_keys:
                report.plans_skipped += 1
                continue
            dose = round(rng.uniform(*tpl["dose"]), 2)
            try:
                validate_treatment_plan(
                    db,
                    cycle=plan_cycle,
                    item=item,
                    dose=dose,
                    operation_type=tpl["op"],
                    planned_date=planned,
                )
            except AppError as exc:  # сид не создаёт планов, отвергаемых API (422)
                # validate_treatment_plan только читает БД — транзакция не
                # испорчена, rollback не нужен (он потерял бы несохранённые
                # строки этого поля); план просто не добавляем
                report.violations.append(
                    f"{farm_field.name} ({culture.name}) {item.name} {planned}: {exc.detail}"
                )
                continue
            approved = rng.random() < APPROVED_SHARE
            db.add(
                TreatmentPlan(
                    cycle_id=plan_cycle.id,
                    input_id=item.id,
                    dose=dose,
                    operation_type=tpl["op"],
                    planned_date=planned,
                    planned_phase=tpl["phase"],
                    status="approved" if approved else "draft",
                )
            )
            db.flush()  # план виден следующим валидаторам (RULE-AGRO-07/10 сезон)
            plan_keys.add(key)
            report.plans_created += 1
            if approved:
                report.plans_approved += 1
            else:
                report.plans_draft += 1
        db.commit()

    logger.info(
        "seed-operations: полей=%d история %d–%d: операций=%d (пропущено=%d); "
        "планы %d: создано=%d (approved=%d, draft=%d, пропущено=%d); нарушений=%d",
        report.fields_processed,
        report.years_from,
        report.years_to,
        report.operations_created,
        report.operations_skipped,
        report.plan_year,
        report.plans_created,
        report.plans_approved,
        report.plans_draft,
        report.plans_skipped,
        len(report.violations),
    )
    return report


__all__ = ["SeedOperationsReport", "seed_operations"]
