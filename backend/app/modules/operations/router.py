"""API журнала операций: API-17/18 + единая операция §7.10 (#173).

Эндпоинты:
- API-17 POST /operations — полиморфен (§7.10, пост-решение архитектора):
  с датой факта (date|fact_date, клиент может дать client_id для
  идемпотентности офлайн-очереди) — внеплановый факт, как раньше;
  без даты факта, но с planned_date — создание ПЛАНА.
- API-18 GET /operations — единый журнал план+факт: фильтры field_id,
  date_from/date_to, type, year (сезон цикла), mode=plan|fact, confirmed.
- §7.10 PATCH /operations/{id} — правка плана (до закрытия фактом);
- §7.10 POST /operations/{id}/complete — закрытие фактом (расход,
  стоимость, техника 1:N, отклонение, урожайность);
- §7.10 POST /operations/{id}/confirm — подтверждение факта (кто закрыл).

Права (07-ux §5: E-13 журнал работ) — группа «operations»:
агроном, руководитель, механизатор, админ.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.db import get_db
from app.core.rbac import group_checker
from app.models.agronomy import FieldOperation
from app.modules.operations import service
from app.modules.operations.schemas import (
    MachineLinkItem,
    OperationCreate,
    OperationItem,
    OperationListResponse,
    UnifiedOperationCompleteIn,
    UnifiedOperationPatchIn,
    UnifiedOperationPlanIn,
    YieldRecordItem,
)

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/operations", tags=["operations"], dependencies=[Depends(group_checker("operations"))]
)


def _item(
    db: Session,
    operation: FieldOperation,
    rates: dict[str, float] | None = None,
    machines: dict[uuid.UUID, list] | None = None,
) -> OperationItem:
    from app.modules.economics.service import cost_rates, operation_cost_rub

    if rates is None:
        rates = cost_rates(db)
    yield_item: YieldRecordItem | None = None
    if operation.operation_type == "harvesting" and operation.cycle_id is not None:
        record = service.yield_of(db, operation)
        if record is not None:
            yield_item = YieldRecordItem(
                yield_c_ha=float(record.yield_c_ha),
                gross_t=float(record.gross_t) if record.gross_t is not None else None,
                source=record.source,
                date=record.date,
            )
    input_name: str | None = None
    if operation.input_id is not None:
        from app.models.agronomy import AgroInput

        row = db.get(AgroInput, operation.input_id)
        input_name = row.name if row is not None else None
    machine_items: list[MachineLinkItem] = []
    if machines is None:
        machines = service.machines_of(db, [operation.id])
    for link, machine_name, trailer_name in machines.get(operation.id, []):
        machine_items.append(
            MachineLinkItem(
                machine_id=link.machine_id,
                trailer_id=link.trailer_id,
                operator=link.operator,
                hours=float(link.hours) if link.hours is not None else None,
                machine_name=machine_name,
                trailer_name=trailer_name,
            )
        )
    return OperationItem(
        operation_id=operation.id,
        field_id=operation.field_id,
        cycle_id=operation.cycle_id,
        operation_type=operation.operation_type,
        date=operation.date,
        input_id=operation.input_id,
        input_dose=float(operation.input_dose) if operation.input_dose is not None else None,
        actual_area_ga=float(operation.actual_area_ga),
        performer=operation.performer,
        machine_job_id=operation.machine_job_id,
        client_id=operation.client_id,
        source=operation.source,
        deviation_flag=operation.deviation_flag,
        deviation_rationale=operation.deviation_rationale,
        needs_review=operation.needs_review,
        status=operation.status,
        yield_record=yield_item,
        cost_rub=operation_cost_rub(
            operation, rates, side="fact" if operation.status == "done" else "plan"
        ),
        planned_date=operation.planned_date,
        fact_date=operation.fact_date,
        input_name=input_name,
        input_amount_total=(
            float(operation.input_amount_total)
            if operation.input_amount_total is not None
            else None
        ),
        cost_plan_rub=operation_cost_rub(operation, rates, side="plan"),
        # §7.10: cost_fact только у закрытого факта (норматив-fallback —
        # для факта); у плана fact-стоимости нет → null (#173, нит #551)
        cost_fact_rub=(
            operation_cost_rub(operation, rates, side="fact")
            if operation.fact_date is not None
            else None
        ),
        confirmed_by=operation.confirmed_by,
        confirmed_at=operation.confirmed_at,
        basis_recommendation_id=operation.basis_recommendation_id,
        machines=machine_items,
    )


@router.post(
    "",
    response_model=OperationItem,
    summary="API-17/§7.10: факт (date/fact_date, офлайн-очередь) или план (planned_date)",
)
def create_operation(
    body: OperationCreate | UnifiedOperationPlanIn,
    response: Response,
    db: DbSession,
) -> OperationItem:
    """Полиморфный POST (§7.10): тело факта (OperationCreate, есть date) —
    внеплановый факт с идемпотентностью client_id; тело плана
    (UnifiedOperationPlanIn, есть planned_date, нет date) — плановая операция.
    """
    if isinstance(body, OperationCreate):
        operation, created = service.create_operation(db, data=body)
        if created:
            response.status_code = 201
            response.headers["Location"] = (
                f"{get_settings().api_v1_prefix}/operations/{operation.id}"
            )
        else:
            # Идемпотентный повтор (тот же client_id): 200, без дубля
            response.status_code = 200
        return _item(db, operation)
    operation = service.create_plan(db, data=body)
    response.status_code = 201
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/operations/{operation.id}"
    return _item(db, operation)


@router.get("/{operation_id}", response_model=OperationItem, summary="API-18: операция по id")
def get_operation(operation_id: uuid.UUID, db: DbSession) -> OperationItem:
    return _item(db, service.get_operation(db, operation_id))


@router.patch(
    "/{operation_id}",
    response_model=OperationItem,
    summary="§7.10: правка плана операции (до закрытия фактом)",
)
def patch_operation(
    operation_id: uuid.UUID, body: UnifiedOperationPatchIn, db: DbSession
) -> OperationItem:
    return _item(db, service.patch_plan(db, operation_id, data=body))


@router.post(
    "/{operation_id}/complete",
    response_model=OperationItem,
    summary="§7.10: закрытие операции фактом (расход, стоимость, техника)",
)
def complete_operation(
    operation_id: uuid.UUID, body: UnifiedOperationCompleteIn, db: DbSession
) -> OperationItem:
    return _item(db, service.complete_operation(db, operation_id, data=body))


@router.post(
    "/{operation_id}/confirm",
    response_model=OperationItem,
    summary="§7.10: подтверждение закрытого факта (идемпотентно)",
)
def confirm_operation(operation_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OperationItem:
    return _item(db, service.confirm_operation(db, operation_id, user_id=user.id))


@router.get(
    "",
    response_model=OperationListResponse,
    summary="API-18: журнал операций (план+факт, фильтры §7.10)",
)
def list_operations(
    db: DbSession,
    *,
    field_id: uuid.UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    operation_type: str | None = Query(default=None, alias="type"),
    year: int | None = Query(default=None, ge=2000, le=2100, description="Сезон цикла"),
    mode: str | None = Query(default=None, pattern="^(plan|fact)$"),
    confirmed: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> OperationListResponse:
    operations, total = service.list_operations(
        db,
        field_id=field_id,
        date_from=date_from,
        date_to=date_to,
        operation_type=operation_type,
        year=year,
        mode=mode,
        confirmed=confirmed,
        limit=limit,
        offset=offset,
    )
    from app.modules.economics.service import cost_rates

    rates = cost_rates(db)
    machines = service.machines_of(db, [o.id for o in operations])
    return OperationListResponse(
        items=[_item(db, o, rates, machines) for o in operations],
        total=total,
        limit=limit,
        offset=offset,
    )
