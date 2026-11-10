"""Экономика сезона поля: стоимость техработ план/факт (§7.9, API-62, #161).

Суммы считаются по операциям журнала dat_field_operation цикла сезона:
план — статусы draft/planned/in_progress, факт — status='done'.
Стоимость операции: cost_mode='unit' → cost_value × площадь операции
(руб/га), 'total' → фикс за поле; не задана → для факта норматив
ref_operation_cost по типу (× га), иначе null — в суммы не входит.
Себестоимость тонны: fact_cost_rub / (yield_c_ha/10 × area_ga), ц/га → т.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agronomy import CropCycle, Field, FieldOperation, YieldRecord
from app.models.references import OperationCost

logger = logging.getLogger(__name__)

#: §7.10: план — planned_date есть/факт не закрыт (кроме cancelled);
#: факт — fact_date закрыт (status='done' по инварианту)
FACT_STATUS = "done"

#: русские подписи типов операций (§7.9; "cultivation" из мока фронта —
#: в модели журнала типы operations §5, культивация = tillage)
OPERATION_TITLES: dict[str, str] = {
    "spraying": "Опрыскивание",
    "fertilizing": "Внесение удобрений",
    "seeding": "Посев",
    "harvesting": "Уборка",
    "tillage": "Обработка почвы",
    "other": "Прочая работа",
}


@dataclass(frozen=True, slots=True)
class CostedOperation:
    """Операция с рассчитанной стоимостью (None — не участвует в суммах)."""

    operation_type: str
    day: date
    cost_rub: float | None


def operation_cost_rub(
    op: FieldOperation,
    rates: dict[str, float] | None = None,
    *,
    is_fact: bool | None = None,
    side: str | None = None,
) -> float | None:
    """Стоимость одной операции (§7.10): план из cost_plan_*, факт из
    cost_fact_*; unit — руб/га (× площадь операции), total — фикс;
    не задана → для ФАКТА норматив ref_operation_cost (× га), для плана —
    None (норматив только факт). side ('plan'|'fact') удобнее is_fact."""
    if side is not None:
        is_fact = side == "fact"
    assert is_fact is not None
    mode = op.cost_fact_mode if is_fact else op.cost_plan_mode
    value = op.cost_fact_value if is_fact else op.cost_plan_value
    if mode == "total" and value is not None:
        return float(value)
    if mode == "unit" and value is not None:
        return round(float(value) * float(op.actual_area_ga), 2)
    if is_fact and rates:
        rate = rates.get(op.operation_type)
        if rate is not None:
            return round(rate * float(op.actual_area_ga), 2)
    return None


def is_plan_op(op: FieldOperation) -> bool:
    """Плановая операция §7.10: факт не закрыт и не отменена."""
    return op.status != "cancelled" and op.fact_date is None


def is_fact_op(op: FieldOperation) -> bool:
    """Выполненная операция §7.10: fact_date закрыт."""
    return op.fact_date is not None


def cost_rates(db: Session) -> dict[str, float]:
    """Нормативы ref_operation_cost: тип операции → руб/га (§7.9)."""
    return {
        op_type: float(rate)
        for op_type, rate in db.execute(
            select(OperationCost.operation_type, OperationCost.rub_per_ga)
        ).all()
    }


def farm_economics(db: Session, year: int) -> dict:
    """Сводка сезона хозяйства: Σ план/факт по всем полям (API-62b, §7.9)."""
    rates = cost_rates(db)
    cycle_ids = [
        cid
        for (cid,) in db.execute(
            select(CropCycle.id).where(CropCycle.year == year, CropCycle.kind == "main")
        ).all()
    ]
    plan_total = 0.0
    fact_total = 0.0
    if cycle_ids:
        ops = db.scalars(select(FieldOperation).where(FieldOperation.cycle_id.in_(cycle_ids))).all()
        for op in ops:
            is_fact = op.status == FACT_STATUS
            if not is_fact and op.status not in PLAN_STATUSES:
                continue  # cancelled и пр. в экономики не входят
            cost = operation_cost_rub(op, rates, is_fact=is_fact)
            if cost is None:
                continue
            if is_fact:
                fact_total += cost
            else:
                plan_total += cost
    return {
        "year": year,
        "plan_cost_rub": round(plan_total, 2),
        "fact_cost_rub": round(fact_total, 2),
    }


def _latest_yield(db: Session, cycle_id: uuid.UUID) -> float | None:
    value = db.scalar(
        select(YieldRecord.yield_c_ha)
        .where(YieldRecord.cycle_id == cycle_id)
        .order_by(YieldRecord.date.desc(), YieldRecord.created_at.desc())
        .limit(1)
    )
    return None if value is None else float(value)


def field_economics(db: Session, field_id: uuid.UUID, year: int) -> dict:
    """Агрегаты экономики цикла сезона (§7.9): план/факт суммы, руб/га,
    руб/т, детализация по типам операций. Цикла нет — пустые суммы."""
    field = db.get(Field, field_id)
    if field is None:
        raise LookupError(f"Поле {field_id} не найдено")
    cycle = db.scalar(
        select(CropCycle).where(
            CropCycle.field_id == field_id,
            CropCycle.year == year,
            CropCycle.kind == "main",
        )
    )
    rates = cost_rates(db)

    plans: list[CostedOperation] = []
    facts: list[CostedOperation] = []
    if cycle is not None:
        ops = db.scalars(
            select(FieldOperation)
            .where(FieldOperation.cycle_id == cycle.id)
            .order_by(FieldOperation.date)
        ).all()
        for op in ops:
            if op.status in PLAN_STATUSES:
                plans.append(
                    CostedOperation(
                        op.operation_type, op.date, operation_cost_rub(op, rates, is_fact=False)
                    )
                )
            elif op.status == FACT_STATUS:
                facts.append(
                    CostedOperation(
                        op.operation_type, op.date, operation_cost_rub(op, rates, is_fact=True)
                    )
                )

    plan_cost = round(sum(p.cost_rub for p in plans if p.cost_rub is not None), 2)
    fact_cost = round(sum(f.cost_rub for f in facts if f.cost_rub is not None), 2)
    area = float(field.area_ga or 0.0)
    cost_per_ga = round(fact_cost / area, 2) if area > 0 and fact_cost else 0.0

    yield_c_ha = _latest_yield(db, cycle.id) if cycle is not None else None
    tons = round(yield_c_ha / 10.0 * area, 2) if yield_c_ha is not None and area > 0 else None
    cost_per_ton = round(fact_cost / tons, 2) if tons else None

    # построчная детализация по типу операции: план/факт сводки
    types: list[str] = []
    for op_list in (plans, facts):
        for item in op_list:
            if item.operation_type not in types:
                types.append(item.operation_type)

    def _summary(items: list[CostedOperation]) -> dict | None:
        if not items:
            return None
        return {
            "date": max(i.day for i in items).isoformat(),
            "cost_rub": round(sum(i.cost_rub for i in items if i.cost_rub is not None), 2),
        }

    rows: list[tuple[date, dict]] = []
    for op_type in types:
        type_plans = [p for p in plans if p.operation_type == op_type]
        type_facts = [f for f in facts if f.operation_type == op_type]
        row = {
            "type": op_type,
            "title": OPERATION_TITLES.get(op_type, op_type),
            "plan": _summary(type_plans),
            "fact": _summary(type_facts),
        }
        rows.append((max((i.day for i in type_facts + type_plans), default=date.min), row))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    operations = [row for _, row in rows]

    return {
        "field_id": field_id,
        "year": year,
        "area_ga": area,
        "plan_cost_rub": plan_cost,
        "fact_cost_rub": fact_cost,
        "cost_per_ga_rub": cost_per_ga,
        "fact_cost_per_ton_rub": cost_per_ton,
        "yield_c_ha": yield_c_ha,
        "operations": operations,
    }


__all__ = [
    "FACT_STATUS",
    "OPERATION_TITLES",
    "PLAN_STATUSES",
    "cost_rates",
    "farm_economics",
    "field_economics",
    "operation_cost_rub",
]
