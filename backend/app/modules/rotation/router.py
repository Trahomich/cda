"""API модуля севооборота: API-08..11 (задача MVP-8, docs/06-data-api.md §5).

Эндпоинты:
- API-08 GET /rotation-plans — список (фильтры field_id, status; пагинация);
- API-09 POST /rotation-plans — создание; ответ содержит violations (dry-run
  необязательного черновика items[]);
- API-10 POST /rotation-plans/{id}/approve — согласование руководителем;
- API-11 POST /crop-cycles — добавить цикл; 422 при нарушении RULE-AGRO-01..04
  (ban — блок; warn — допустим с reason агронома, возвращается в warnings).

Права (07-ux §5): группа «rotation» (агроном, руководитель, админ) — чтение;
создание планов/циклов — agronomist/admin; согласование — manager/admin.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.rbac import group_checker, role_checker
from app.models.auth import User, UserRole
from app.models.references import Culture
from app.modules.rotation import service
from app.modules.rotation.schemas import (
    CycleCreate,
    CycleDetail,
    CycleItem,
    PlanCreate,
    PlanDetail,
    PlanListItem,
    PlanListResponse,
    PlanViolationItem,
    PlanViolationsResponse,
    ViolationItem,
)

DbSession = Annotated[Session, Depends(get_db)]
WriteAccess = Annotated[User, Depends(role_checker(UserRole.AGRONOMIST, UserRole.ADMIN))]
ApproveAccess = Annotated[User, Depends(role_checker(UserRole.MANAGER, UserRole.ADMIN))]

router = APIRouter(tags=["rotation"], dependencies=[Depends(group_checker("rotation"))])


def _cycle_item(db: DbSession, cycle, warnings: list[ViolationItem] | None = None) -> CycleDetail:
    culture = db.get_one(Culture, cycle.culture_id)
    return CycleDetail(
        cycle_id=cycle.id,
        field_id=cycle.field_id,
        year=cycle.year,
        culture_id=cycle.culture_id,
        culture_name=culture.name,
        kind=cycle.kind,
        predecessor_cycle_id=cycle.predecessor_cycle_id,
        planned_yield_c_ha=(
            float(cycle.planned_yield_c_ha) if cycle.planned_yield_c_ha is not None else None
        ),
        planned_harvest_date=cycle.planned_harvest_date,
        out_of_rotation=cycle.out_of_rotation,
        reason=cycle.reason,
        status=cycle.status,
        warnings=warnings or [],
    )


def _plan_detail(db: DbSession, plan, violations: list[ViolationItem] | None = None) -> PlanDetail:
    return PlanDetail(
        plan_id=plan.id,
        field_id=plan.field_id,
        year_from=plan.year_from,
        year_to=plan.year_to,
        status=plan.status,
        approved_by=plan.approved_by,
        approved_at=plan.approved_at,
        items=[
            CycleItem(
                cycle_id=c.id,
                field_id=c.field_id,
                year=c.year,
                culture_id=c.culture_id,
                culture_name=db.get_one(Culture, c.culture_id).name,
                kind=c.kind,
                predecessor_cycle_id=c.predecessor_cycle_id,
                planned_yield_c_ha=(
                    float(c.planned_yield_c_ha) if c.planned_yield_c_ha is not None else None
                ),
                planned_harvest_date=c.planned_harvest_date,
                out_of_rotation=c.out_of_rotation,
                reason=c.reason,
                status=c.status,
            )
            for c in service.plan_cycles(db, plan)
        ],
        violations=violations or [],
    )


@router.get("/rotation-plans", response_model=PlanListResponse, summary="API-08: планы севооборота")
def list_plans(
    db: DbSession,
    *,
    field_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PlanListResponse:
    plans, total = service.list_plans(
        db, field_id=field_id, status=status, limit=limit, offset=offset
    )
    return PlanListResponse(
        items=[_plan_detail(db, p) for p in plans],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/rotation-plans",
    response_model=PlanDetail,
    status_code=201,
    summary="API-09: создать план; ответ содержит список нарушений",
)
def create_plan(
    body: PlanCreate,
    response: Response,
    user: WriteAccess,  # noqa: ARG001 (роль проверена зависимостью)
    db: DbSession,
) -> PlanDetail:
    plan, violations = service.create_plan(
        db,
        user=user,
        field_id=body.field_id,
        year_from=body.year_from,
        year_to=body.year_to,
        items=body.items,
    )
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/rotation-plans/{plan.id}"
    return _plan_detail(db, plan, violations=[ViolationItem(**vars(v)) for v in violations])


@router.post(
    "/rotation-plans/{plan_id}/approve",
    response_model=PlanDetail,
    summary="API-10: согласование руководителем (draft → approved)",
)
def approve_plan(plan_id: uuid.UUID, user: ApproveAccess, db: DbSession) -> PlanDetail:
    plan = service.get_plan(db, plan_id)
    plan = service.approve_plan(db, user=user, plan=plan)
    return _plan_detail(db, plan)


@router.post(
    "/crop-cycles",
    response_model=CycleDetail,
    status_code=201,
    summary="API-11: добавить цикл культуры (RULE-AGRO-01..04)",
)
def create_cycle(
    body: CycleCreate,
    response: Response,
    user: WriteAccess,  # noqa: ARG001 (роль проверена зависимостью)
    db: DbSession,
) -> CycleDetail:
    cycle, warnings = service.create_cycle(
        db,
        user=user,
        field_id=body.field_id,
        year=body.year,
        culture_id=body.culture_id,
        kind=body.kind,
        planned_yield_c_ha=body.planned_yield_c_ha,
        planned_harvest_date=body.planned_harvest_date,
        out_of_rotation=body.out_of_rotation,
        reason=body.reason,
    )
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/crop-cycles/{cycle.id}"
    return _cycle_item(db, cycle, warnings=[ViolationItem(**vars(v)) for v in warnings])
