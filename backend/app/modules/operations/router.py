"""API журнала операций: API-17/18 (задача MVP-9, docs/06-data-api.md §5).

Эндпоинты:
- API-17 POST /operations — запись факта операции (мобильный, офлайн-очередь
  PWA). client_id — идемпотентность: повтор возвращает существующую операцию
  (200), новый факт — 201 + Location. Урожайность внутри операции уборки.
- API-18 GET /operations?field_id&date_from&date_to — журнал с фильтрами
  и пагинацией (items/total/limit/offset).

Права (07-ux §5: E-13 журнал работ) — группа «operations»:
агроном, руководитель, механизатор, админ.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.rbac import group_checker
from app.models.agronomy import FieldOperation
from app.modules.operations import service
from app.modules.operations.schemas import (
    OperationCreate,
    OperationItem,
    OperationListResponse,
    YieldRecordItem,
)

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/operations", tags=["operations"], dependencies=[Depends(group_checker("operations"))]
)


def _item(db: Session, operation: FieldOperation) -> OperationItem:
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
    return OperationItem(
        operation_id=operation.id,
        field_id=operation.field_id,
        cycle_id=operation.cycle_id,
        operation_type=operation.operation_type,
        date=operation.date,
        input_id=operation.input_id,
        actual_dose=float(operation.actual_dose) if operation.actual_dose is not None else None,
        actual_area_ga=float(operation.actual_area_ga),
        performer=operation.performer,
        machine_job_id=operation.machine_job_id,
        client_id=operation.client_id,
        source=operation.source,
        deviation_flag=operation.deviation_flag,
        deviation_rationale=operation.deviation_rationale,
        status=operation.status,
        yield_record=yield_item,
    )


@router.post(
    "",
    response_model=OperationItem,
    summary="API-17: запись факта операции (офлайн-очередь PWA)",
)
def create_operation(
    body: OperationCreate,
    response: Response,
    db: DbSession,
) -> OperationItem:
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


@router.get(
    "/{operation_id}", response_model=OperationItem, summary="API-18: операция по id"
)
def get_operation(operation_id: uuid.UUID, db: DbSession) -> OperationItem:
    return _item(db, service.get_operation(db, operation_id))


@router.get(
    "",
    response_model=OperationListResponse,
    summary="API-18: журнал операций (фильтры field_id, date_from, date_to)",
)
def list_operations(
    db: DbSession,
    *,
    field_id: uuid.UUID | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    operation_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> OperationListResponse:
    operations, total = service.list_operations(
        db,
        field_id=field_id,
        date_from=date_from,
        date_to=date_to,
        operation_type=operation_type,
        limit=limit,
        offset=offset,
    )
    return OperationListResponse(
        items=[_item(db, o) for o in operations], total=total, limit=limit, offset=offset
    )
