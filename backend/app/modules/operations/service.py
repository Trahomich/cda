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
from datetime import date
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
from app.models.machinery import MachineJob
from app.modules.operations.schemas import (
    OPERATION_SOURCES,
    OPERATION_TYPES,
    OperationCreate,
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
    if data.actual_dose is not None:
        plan = _find_plan(db, data)
        if plan is not None and float(plan.dose) > 0:
            # Decimal: |факт − план| / план строго > 10 % без ошибок float
            delta = abs(Decimal(str(data.actual_dose)) - Decimal(str(plan.dose))) / Decimal(
                str(plan.dose)
            )
            if delta > Decimal("0.10"):
                deviation_flag = True
                rationale = _deviation_rationale(data.actual_dose, float(plan.dose))

    operation = FieldOperation(
        client_id=data.client_id,
        field_id=data.field_id,
        cycle_id=data.cycle_id,
        operation_type=data.operation_type,
        date=data.date,
        input_id=data.input_id,
        actual_dose=data.actual_dose,
        actual_area_ga=data.actual_area_ga,
        performer=data.performer,
        machine_job_id=data.machine_job_id,
        source=data.source,
        deviation_flag=deviation_flag,
        deviation_rationale=rationale,
        needs_review=_needs_review(db, field, data),
        status="done",
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
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[FieldOperation], int]:
    """API-18: GET /operations — журнал с фильтрами, новые сверху."""
    if operation_type is not None and operation_type not in OPERATION_TYPES:
        raise AppError(
            f"Неизвестный тип операции «{operation_type}» "
            f"(допустимо: {', '.join(OPERATION_TYPES)})",
            code="unknown_operation_type",
        )
    stmt = select(FieldOperation).order_by(
        FieldOperation.date.desc(), FieldOperation.created_at.desc(), FieldOperation.id
    )
    count_stmt = select(func.count()).select_from(FieldOperation)
    if field_id is not None:
        stmt, count_stmt = (
            stmt.where(FieldOperation.field_id == field_id),
            count_stmt.where(FieldOperation.field_id == field_id),
        )
    if date_from is not None:
        stmt, count_stmt = (
            stmt.where(FieldOperation.date >= date_from),
            count_stmt.where(FieldOperation.date >= date_from),
        )
    if date_to is not None:
        stmt, count_stmt = (
            stmt.where(FieldOperation.date <= date_to),
            count_stmt.where(FieldOperation.date <= date_to),
        )
    if operation_type is not None:
        stmt, count_stmt = (
            stmt.where(FieldOperation.operation_type == operation_type),
            count_stmt.where(FieldOperation.operation_type == operation_type),
        )
    return list(db.scalars(stmt.limit(limit).offset(offset))), db.scalar(count_stmt) or 0
