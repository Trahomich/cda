"""Сервис журнала операций (задача MVP-9, API-17/18).

Сущности и правила — docs/03-domain-agronomy.md §1.6 (ENT-FIELD-OPERATION),
§1.7 (ENT-YIELD-RECORD), §3.4 (RULE-AGRO-11). Офлайн-очередь PWA —
docs/10-mobile-pwa.md §3: «факт важнее плана» — 422 при расхождении
не возвращаем, помечаем deviation_flag и сохраняем rationale.

Ошибки — RFC 7807 (app.core.errors):
  400 unknown_operation_type / unknown_operation_source;
  404 not_found (поле, цикл, препарат, работа техники);
  409 duplicate_yield_record (UQ cycle_id+date+source, 03 §1.7).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.agronomy import (
    AgroInput,
    CropCycle,
    Field,
    FieldOperation,
    FieldVersion,
    TreatmentPlan,
    YieldRecord,
)
from app.models.machinery import Machine, MachineJob, OperationMachine
from app.modules.operations.schemas import (
    OPERATION_SOURCES,
    OPERATION_TYPES,
    MachineLinkIn,
    OperationCreate,
    UnifiedOperationCompleteIn,
    UnifiedOperationPatchIn,
    UnifiedOperationPlanIn,
)

# RULE-AGRO-11: расхождение факт/план больше 10 % — отклонение
DEVIATION_THRESHOLD = 0.10
# Статусы планов, относительно которых считается отклонение (черновики
# и отменённые планы — не основание для отклонения)
PLAN_STATUSES = ("approved", "in_progress", "completed")


def get_operation(db: Session, operation_id: uuid.UUID) -> FieldOperation:
    operation = db.get(FieldOperation, operation_id)
    if operation is None:
        raise AppError(
            f"Операция {operation_id} не найдена",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return operation


def yield_of(db: Session, operation: FieldOperation) -> YieldRecord | None:
    """Урожайность операции уборки: ENT-YIELD-RECORD того же цикла и даты."""
    if operation.cycle_id is None:
        return None
    return db.scalars(
        select(YieldRecord)
        .where(YieldRecord.cycle_id == operation.cycle_id, YieldRecord.date == operation.date)
        .order_by(YieldRecord.id)
        .limit(1)
    ).first()


# RULE-AGRO-12: фактическая площадь не больше 1,05 × площади поля
AREA_FACTOR = Decimal("1.05")


def field_area_on(db: Session, field: Field, on_date: date) -> Decimal:
    """Площадь поля по последней версии контура на дату операции;
    если версий на эту дату нет — текущая площадь контура (03 §1.2)."""
    version = db.scalars(
        select(FieldVersion)
        .where(FieldVersion.field_id == field.id, FieldVersion.valid_from <= on_date)
        .order_by(FieldVersion.valid_from.desc(), FieldVersion.version_no.desc())
        .limit(1)
    ).first()
    return Decimal(str(version.area_ga if version is not None else field.area_ga))


def _needs_review(db: Session, field: Field, data: OperationCreate) -> bool:
    """RULE-AGRO-12: actual_area_ga > 1,05 × площади поля на дату операции."""
    area = field_area_on(db, field, data.date)
    return Decimal(str(data.actual_area_ga)) > AREA_FACTOR * area


def _deviation_rationale(actual: float, planned: float) -> str:
    """Текст обоснования отклонения (RULE-AGRO-11, 03 §3.4)."""
    percent = (actual - planned) / planned * 100
    sign = "+" if percent >= 0 else ""
    return f"Факт {actual:g} против плана {planned:g} ({sign}{percent:.0f} % > 10 %, RULE-AGRO-11)"


def _find_plan(db: Session, data: OperationCreate) -> TreatmentPlan | None:
    """План, относительно которого считается отклонение: тот же цикл,
    препарат и тип операции, статус из PLAN_STATUSES; из подходящих —
    ближайший по плановой дате к дате факта (иначе последний созданный)."""
    if data.cycle_id is None or data.input_id is None:
        return None
    stmt = (
        select(TreatmentPlan)
        .where(
            TreatmentPlan.cycle_id == data.cycle_id,
            TreatmentPlan.input_id == data.input_id,
            TreatmentPlan.operation_type == data.operation_type,
            TreatmentPlan.status.in_(PLAN_STATUSES),
        )
        .order_by(func.abs(func.coalesce(TreatmentPlan.planned_date, data.date) - data.date))
    )
    return db.scalars(stmt).first()


def create_operation(db: Session, *, data: OperationCreate) -> tuple[FieldOperation, bool]:
    """API-17: POST /operations — запись факта операции.

    Идемпотентность офлайн-очереди: при повторном client_id возвращается
    существующая операция (created=False → роутер отвечает 200 без дубля).
    Урожайность (yield_record) для типа harvesting создаётся в той же
    транзакции (ENT-YIELD-RECORD, UQ cycle_id+date+source).
    """
    if data.client_id is not None:
        existing = db.scalars(
            select(FieldOperation).where(FieldOperation.client_id == data.client_id)
        ).first()
        if existing is not None:
            return existing, False

    if data.operation_type not in OPERATION_TYPES:
        raise AppError(
            f"Неизвестный тип операции «{data.operation_type}» "
            f"(допустимо: {', '.join(OPERATION_TYPES)})",
            code="unknown_operation_type",
        )
    if data.source not in OPERATION_SOURCES:
        raise AppError(
            f"Неизвестный источник записи «{data.source}» "
            f"(допустимо: {', '.join(OPERATION_SOURCES)})",
            code="unknown_operation_source",
        )

    # FK проверяем до вставки (иначе IntegrityError → 500)
    field = db.get(Field, data.field_id)
    if field is None:
        raise AppError(
            f"Поле {data.field_id} не найдено",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    if data.cycle_id is not None and db.get(CropCycle, data.cycle_id) is None:
        raise AppError(
            f"Цикл {data.cycle_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    if data.input_id is not None and db.get(AgroInput, data.input_id) is None:
        raise AppError(
            f"Препарат {data.input_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    if data.machine_job_id is not None and db.get(MachineJob, data.machine_job_id) is None:
        raise AppError(
            f"Работа техники {data.machine_job_id} не найдена",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )

    # Урожайность — только для операции уборки и только в цикле (03 §1.7)
    if data.yield_record is not None:
        if data.operation_type != "harvesting":
            raise AppError(
                "Урожайность принимается только для операции типа harvesting",
                code="yield_requires_harvesting",
                title="Нарушено бизнес-правило",
                status_code=422,
            )
        if data.cycle_id is None:
            raise AppError(
                "Для записи урожайности обязателен cycle_id (ENT-YIELD-RECORD)",
                code="yield_requires_cycle",
                title="Нарушено бизнес-правило",
                status_code=422,
            )

    # «Факт важнее плана» (10-pwa §3): 422 не возвращаем — помечаем отклонение
    deviation_flag = False
    rationale: str | None = None
    if data.input_dose is not None:
        plan = _find_plan(db, data)
        if plan is not None and float(plan.dose) > 0:
            # Decimal: |факт − план| / план строго > 10 % без ошибок float
            delta = abs(Decimal(str(data.input_dose)) - Decimal(str(plan.dose))) / Decimal(
                str(plan.dose)
            )
            if delta > Decimal("0.10"):
                deviation_flag = True
                rationale = _deviation_rationale(data.input_dose, float(plan.dose))

    operation = FieldOperation(
        client_id=data.client_id,
        field_id=data.field_id,
        cycle_id=data.cycle_id,
        operation_type=data.operation_type,
        date=data.date,
        input_id=data.input_id,
        input_dose=data.input_dose,
        actual_area_ga=data.actual_area_ga,
        performer=data.performer,
        machine_job_id=data.machine_job_id,
        source=data.source,
        deviation_flag=deviation_flag,
        deviation_rationale=rationale,
        needs_review=_needs_review(db, field, data),
        status="done",
        fact_date=data.date,  # §7.10: факт закрыт датой операции
    )
    db.add(operation)
    if data.yield_record is not None:
        db.add(
            YieldRecord(
                cycle_id=data.cycle_id,
                yield_c_ha=data.yield_record.yield_c_ha,
                gross_t=data.yield_record.gross_t,
                source=data.yield_record.source,
                date=data.date,
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Гонка идемпотентности: параллельный POST с тем же client_id уже вставил
        if data.client_id is not None:
            existing = db.scalars(
                select(FieldOperation).where(FieldOperation.client_id == data.client_id)
            ).first()
            if existing is not None:
                return existing, False
        raise AppError(
            "Запись урожайности с этой датой и источником для цикла уже существует",
            code="duplicate_yield_record",
            title="Конфликт состояния",
            status_code=409,
        ) from exc
    db.refresh(operation)
    return operation, True


def list_operations(
    db: Session,
    *,
    field_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    operation_type: str | None = None,
    year: int | None = None,
    mode: str | None = None,
    confirmed: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[FieldOperation], int]:
    """API-18: GET /operations — журнал с фильтрами, новые сверху.

    §7.10 (#173): year — сезон цикла; mode=plan|fact — по наличию дат
    (plan: planned_date ≠ null ∧ fact_date = null; fact: fact_date ≠ null);
    confirmed — по confirmed_at. date_from/date_to — по date (fact ?? planned).
    """
    if operation_type is not None and operation_type not in OPERATION_TYPES:
        raise AppError(
            f"Неизвестный тип операции «{operation_type}» "
            f"(допустимо: {', '.join(OPERATION_TYPES)})",
            code="unknown_operation_type",
        )
    if mode is not None and mode not in ("plan", "fact"):
        raise AppError(
            "Параметр mode допускает значения plan | fact",
            code="invalid_mode",
            title="Некорректный запрос",
            status_code=422,
        )
    stmt = select(FieldOperation).order_by(
        FieldOperation.date.desc(), FieldOperation.created_at.desc(), FieldOperation.id
    )
    count_stmt = select(func.count()).select_from(FieldOperation)
    filters = []
    if field_id is not None:
        filters.append(FieldOperation.field_id == field_id)
    if date_from is not None:
        filters.append(FieldOperation.date >= date_from)
    if date_to is not None:
        filters.append(FieldOperation.date <= date_to)
    if operation_type is not None:
        filters.append(FieldOperation.operation_type == operation_type)
    if mode == "plan":
        filters.append(FieldOperation.planned_date.isnot(None))
        filters.append(FieldOperation.fact_date.is_(None))
    elif mode == "fact":
        filters.append(FieldOperation.fact_date.isnot(None))
    if confirmed is not None:
        is_confirmed = (
            FieldOperation.confirmed_at.isnot(None)
            if confirmed
            else FieldOperation.confirmed_at.is_(None)
        )
        filters.append(is_confirmed)
    if year is not None:
        stmt = stmt.join(CropCycle, FieldOperation.cycle_id == CropCycle.id)
        count_stmt = count_stmt.join(CropCycle, FieldOperation.cycle_id == CropCycle.id)
        filters.append(CropCycle.year == year)
    for flt in filters:
        stmt, count_stmt = stmt.where(flt), count_stmt.where(flt)
    return list(db.scalars(stmt.limit(limit).offset(offset))), db.scalar(count_stmt) or 0


# -- Единая операция (§7.10, #173): план → факт → подтверждение, техника 1:N --


#: сезон цикла по дате: год уборки (озимые с осени — следующий год), 03 §1.4
def _season_cycle(db: Session, field_id: uuid.UUID, day: date) -> uuid.UUID | None:
    years = (day.year + 1, day.year) if day.month >= 9 else (day.year, day.year - 1)
    for year in years:
        cycle = db.scalars(
            select(CropCycle)
            .where(
                CropCycle.field_id == field_id,
                CropCycle.year == year,
                CropCycle.kind == "main",
            )
            .limit(1)
        ).first()
        if cycle is not None:
            return cycle.id
    return None


def create_plan(db: Session, *, data: UnifiedOperationPlanIn) -> FieldOperation:
    """§7.10 POST /operations (план-режим): плановая операция.

    Площадь плана = площадь поля на плановую дату; status='planned';
    cycle_id не задан — main-цикл сезона плановой даты (если есть).
    """
    if data.operation_type not in OPERATION_TYPES:
        raise AppError(
            f"Неизвестный тип операции «{data.operation_type}» "
            f"(допустимо: {', '.join(OPERATION_TYPES)})",
            code="unknown_operation_type",
        )
    field = db.get(Field, data.field_id)
    if field is None:
        raise AppError(
            f"Поле {data.field_id} не найдено",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    cycle_id = data.cycle_id or _season_cycle(db, data.field_id, data.planned_date)
    if data.cycle_id is not None and db.get(CropCycle, data.cycle_id) is None:
        raise AppError(
            f"Цикл {data.cycle_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    if data.input_id is not None and db.get(AgroInput, data.input_id) is None:
        raise AppError(
            f"Препарат {data.input_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    from app.models.agronomy import Recommendation

    if (
        data.basis_recommendation_id is not None
        and db.get(Recommendation, data.basis_recommendation_id) is None
    ):
        raise AppError(
            f"Рекомендация {data.basis_recommendation_id} не найдена",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    operation = FieldOperation(
        field_id=data.field_id,
        cycle_id=cycle_id,
        operation_type=data.operation_type,
        date=data.planned_date,
        planned_date=data.planned_date,
        input_id=data.input_id,
        input_dose=data.input_dose,
        input_amount_total=data.input_amount_total,
        actual_area_ga=field_area_on(db, field, data.planned_date),
        performer=data.performer,
        source="manual",
        status="planned",
        cost_plan_mode=data.cost_plan_mode,
        cost_plan_value=data.cost_plan_value,
        basis_recommendation_id=data.basis_recommendation_id,
    )
    db.add(operation)
    db.commit()
    db.refresh(operation)
    return operation


def _assert_not_closed(operation: FieldOperation) -> None:
    if operation.status == "cancelled":
        raise AppError(
            "Операция отменена — правка плана недоступна",
            code="operation_cancelled",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    if operation.fact_date is not None:
        raise AppError(
            "Операция уже закрыта фактом — правка плана недоступна (§7.10)",
            code="operation_already_done",
            title="Нарушено бизнес-правило",
            status_code=422,
        )


def patch_plan(
    db: Session, operation_id: uuid.UUID, *, data: UnifiedOperationPatchIn
) -> FieldOperation:
    """§7.10 PATCH /operations/{id}: правка плана до закрытия фактом."""
    operation = get_operation(db, operation_id)
    _assert_not_closed(operation)
    if data.planned_date is not None:
        operation.planned_date = data.planned_date
        operation.date = data.planned_date
    if data.input_id is not None:
        if db.get(AgroInput, data.input_id) is None:
            raise AppError(
                f"Препарат {data.input_id} не найден",
                code="not_found",
                title="Ресурс не найден",
                status_code=404,
            )
        operation.input_id = data.input_id
    for field_name, value in (
        ("input_dose", data.input_dose),
        ("input_amount_total", data.input_amount_total),
        ("cost_plan_mode", data.cost_plan_mode),
        ("cost_plan_value", data.cost_plan_value),
        ("performer", data.performer),
    ):
        if value is not None:
            setattr(operation, field_name, value)
    if operation.status == "draft" and operation.planned_date is not None:
        operation.status = "planned"
    db.commit()
    db.refresh(operation)
    return operation


def replace_machine_links(
    db: Session, operation: FieldOperation, machines: list[MachineLinkIn]
) -> None:
    """Техника операции 1:N (§7.10): полная замена набора связей."""
    for link in machines:
        if db.get(Machine, link.machine_id) is None:
            raise AppError(
                f"Машина {link.machine_id} не найдена",
                code="not_found",
                title="Ресурс не найден",
                status_code=404,
            )
        if link.trailer_id is not None and db.get(Machine, link.trailer_id) is None:
            raise AppError(
                f"Прицеп {link.trailer_id} не найден",
                code="not_found",
                title="Ресурс не найден",
                status_code=404,
            )
    db.query(OperationMachine).filter(OperationMachine.operation_id == operation.id).delete()
    for link in machines:
        db.add(
            OperationMachine(
                operation_id=operation.id,
                machine_id=link.machine_id,
                trailer_id=link.trailer_id,
                operator=link.operator,
                hours=link.hours,
            )
        )


def complete_operation(
    db: Session,
    operation_id: uuid.UUID,
    *,
    data: UnifiedOperationCompleteIn,
) -> FieldOperation:
    """§7.10 POST /operations/{id}/complete — закрытие фактом.

    Расход всего: input_amount_total, иначе доза × площадь поля на дату факта.
    Стоимость факта: cost_fact_*; не задана → NULL (норматив применит экономика).
    """
    operation = get_operation(db, operation_id)
    if operation.status == "cancelled":
        raise AppError(
            "Операция отменена — закрытие фактом недоступно",
            code="operation_cancelled",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    if operation.fact_date is not None:
        raise AppError(
            "Операция уже закрыта фактом",
            code="operation_already_done",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    field = db.get(Field, operation.field_id)
    assert field is not None
    area = field_area_on(db, field, data.fact_date)

    if data.yield_record is not None:
        if operation.operation_type != "harvesting":
            raise AppError(
                "Урожайность принимается только для операции типа harvesting",
                code="yield_requires_harvesting",
                title="Нарушено бизнес-правило",
                status_code=422,
            )
        if operation.cycle_id is None:
            raise AppError(
                "Для записи урожайности обязателен cycle_id (ENT-YIELD-RECORD)",
                code="yield_requires_cycle",
                title="Нарушено бизнес-правило",
                status_code=422,
            )

    operation.fact_date = data.fact_date
    operation.date = data.fact_date
    operation.status = "done"
    operation.actual_area_ga = area
    if data.input_dose is not None:
        operation.input_dose = data.input_dose
    if data.input_amount_total is not None:
        operation.input_amount_total = data.input_amount_total
    elif operation.input_dose is not None:
        operation.input_amount_total = round(float(operation.input_dose) * float(area), 2)
    if data.cost_fact_mode is not None:
        operation.cost_fact_mode = data.cost_fact_mode
    if data.cost_fact_value is not None:
        operation.cost_fact_value = data.cost_fact_value
    if data.performer is not None:
        operation.performer = data.performer
    if data.deviation_flag:
        operation.deviation_flag = True
    if data.deviation_rationale is not None:
        operation.deviation_rationale = data.deviation_rationale
    replace_machine_links(db, operation, data.machines)
    if data.yield_record is not None:
        db.add(
            YieldRecord(
                cycle_id=operation.cycle_id,
                yield_c_ha=data.yield_record.yield_c_ha,
                gross_t=data.yield_record.gross_t,
                source=data.yield_record.source,
                date=data.fact_date,
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "Запись урожайности с этой датой и источником для цикла уже существует",
            code="duplicate_yield_record",
            title="Конфликт состояния",
            status_code=409,
        ) from exc
    db.refresh(operation)
    return operation


def confirm_operation(
    db: Session, operation_id: uuid.UUID, *, user_id: uuid.UUID
) -> FieldOperation:
    """§7.10 POST /operations/{id}/confirm — подтверждение факта.

    Идемпотентно: повторный вызов не меняет подтверждение. Подтверждается
    только закрытый факт (fact_date ≠ null).
    """
    operation = get_operation(db, operation_id)
    if operation.fact_date is None:
        raise AppError(
            "Подтверждается только закрытый факт (fact_date пуст, §7.10)",
            code="operation_not_done",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    if operation.confirmed_by is None:
        operation.confirmed_by = user_id
        operation.confirmed_at = datetime.now(UTC)
        db.commit()
        db.refresh(operation)
    return operation


def machines_of(
    db: Session, operation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[OperationMachine, str, str | None]]]:
    """Техника операций одним запросом: {operation_id: [(link, машина, прицеп)]}."""
    out: dict[uuid.UUID, list[tuple[OperationMachine, str, str | None]]] = {}
    if not operation_ids:
        return out
    trailer = Machine.__table__.alias("trailer")
    rows = db.execute(
        select(OperationMachine, Machine.name, trailer.c.name)
        .join(Machine, OperationMachine.machine_id == Machine.id)
        .outerjoin(trailer, OperationMachine.trailer_id == trailer.c.id)
        .where(OperationMachine.operation_id.in_(operation_ids))
        .order_by(OperationMachine.id)
    ).all()
    for link, machine_name, trailer_name in rows:
        out.setdefault(link.operation_id, []).append((link, machine_name, trailer_name))
    return out
