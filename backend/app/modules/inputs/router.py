"""API модуля препаратов (задача MVP-11, docs/06-data-api.md §5).

Эндпоинты:
- API-14 GET /agro-inputs, POST /agro-inputs — справочник удобрений и СЗР;
- API-15 GET /treatment-plans, POST /treatment-plans — планы внесения;
  нарушения RULE-AGRO-07/08/10 — 422 rule_violation (RFC 7807 + extra);
- API-16 POST /treatment-plans/{id}/approve — согласование руководителем
  (draft → approved, правила перепроверяются; повтор — 409 invalid_state);
- для operation_type=spraying ответы POST/approve дополняются погодным окном
  WEATHER-WINDOW (§7.2, задача #74): {ok, detail, suggested_dates} | null —
  подсказка по кэшу прогноза, НЕ блокирующая создание/согласование.

Права (07-ux §5): группа «inputs» (агроном, руководитель, админ) — чтение;
создание — agronomist/admin; согласование — manager/admin.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.rbac import group_checker, role_checker
from app.models.agronomy import AgroInput, CropCycle, Field, FieldOperation
from app.models.auth import User, UserRole
from app.models.references import Culture
from app.modules.inputs import service
from app.modules.inputs.schemas import (
    InputCreate,
    InputItem,
    InputListResponse,
    TreatmentPlanCreate,
    TreatmentPlanItem,
    TreatmentPlanListResponse,
    WeatherWindow,
)
from app.modules.weather.window import weather_window_for_plan

DbSession = Annotated[Session, Depends(get_db)]
WriteAccess = Annotated[User, Depends(role_checker(UserRole.AGRONOMIST, UserRole.ADMIN))]
ApproveAccess = Annotated[User, Depends(role_checker(UserRole.MANAGER, UserRole.ADMIN))]

router = APIRouter(tags=["inputs"], dependencies=[Depends(group_checker("inputs"))])


def _input_item(item: AgroInput) -> InputItem:
    npk = service.input_npk(item)
    return InputItem(
        input_id=item.id,
        kind=item.kind,
        name=item.name,
        npk_content=npk,
        active_substance=item.active_substance,
        concentration=float(item.concentration) if item.concentration is not None else None,
        unit=item.unit,
        waiting_days=item.waiting_days,
        approved_crops=list(item.approved_crops or []),
        max_treatments_per_season=item.max_treatments_per_season,
        restrictions=item.restrictions,
    )


def _plan_row(db: Session, plan: FieldOperation) -> tuple:
    """(операция, цикл, поле, культура, препарат) — как строки list_treatment_plans."""
    cycle = db.get_one(CropCycle, plan.cycle_id)
    return (
        plan,
        cycle,
        db.get_one(Field, cycle.field_id),
        db.get_one(Culture, cycle.culture_id),
        db.get_one(AgroInput, plan.input_id),
    )


def _plan_item(row: tuple) -> TreatmentPlanItem:
    """Легаси-форма API-15 из плановой операции (адаптер §7.10):
    dose = input_dose, статус — мэппинг service.treatment_plan_status."""
    plan, cycle, field, culture, item = row
    return TreatmentPlanItem(
        plan_id=plan.id,
        cycle_id=cycle.id,
        field_id=field.id,
        field_name=field.name,
        year=cycle.year,
        culture_name=culture.name,
        input_id=item.id,
        input_name=item.name,
        input_kind=item.kind,
        unit=item.unit,
        dose=float(plan.input_dose or 0.0),
        operation_type=plan.operation_type,
        planned_date=plan.planned_date,
        planned_phase=None,  # фазы в единой операции нет (§7.10)
        status=service.treatment_plan_status(plan),
    )


def _plan_window(db: Session, cycle: CropCycle, plan: FieldOperation) -> WeatherWindow | None:
    """WEATHER-WINDOW (§7.2) для планов spraying; общая логика POST (API-15)
    и approve (API-16). null — не spraying (без даты) или нет прогноза в кэше."""
    if plan.operation_type != "spraying" or plan.planned_date is None:
        return None
    result = weather_window_for_plan(db, field_id=cycle.field_id, planned_date=plan.planned_date)
    if result is None:
        return None
    return WeatherWindow(
        ok=result.ok,
        detail=result.detail,
        suggested_dates=result.suggested_dates,
    )


@router.get(
    "/agro-inputs", response_model=InputListResponse, summary="API-14: справочник препаратов"
)
def list_inputs(
    db: DbSession,
    *,
    kind: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> InputListResponse:
    items, total = service.list_inputs(db, kind=kind, q=q, limit=limit, offset=offset)
    return InputListResponse(
        items=[_input_item(i) for i in items], total=total, limit=limit, offset=offset
    )


@router.post(
    "/agro-inputs",
    response_model=InputItem,
    status_code=201,
    summary="API-14: добавить препарат/удобрение в справочник",
)
def create_input(
    body: InputCreate, response: Response, user: WriteAccess, db: DbSession
) -> InputItem:
    item = service.create_input(
        db,
        user=user,
        kind=body.kind,
        name=body.name,
        npk_content=body.npk_content,
        active_substance=body.active_substance,
        concentration=body.concentration,
        unit=body.unit,
        waiting_days=body.waiting_days,
        approved_crops=body.approved_crops,
        max_treatments_per_season=body.max_treatments_per_season,
        restrictions=body.restrictions,
    )
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/agro-inputs/{item.id}"
    return _input_item(item)


@router.get(
    "/treatment-plans",
    response_model=TreatmentPlanListResponse,
    summary="API-15: планы внесения (фильтры status, cycle_id, field_id)",
)
def list_treatment_plans(
    db: DbSession,
    *,
    status: str | None = None,
    cycle_id: uuid.UUID | None = None,
    field_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TreatmentPlanListResponse:
    rows, total = service.list_treatment_plans(
        db, status=status, cycle_id=cycle_id, field_id=field_id, limit=limit, offset=offset
    )
    return TreatmentPlanListResponse(
        items=[_plan_item(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post(
    "/treatment-plans",
    response_model=TreatmentPlanItem,
    status_code=201,
    summary="API-15: создать план внесения (RULE-AGRO-07/08/10)",
)
def create_treatment_plan(
    body: TreatmentPlanCreate, response: Response, user: WriteAccess, db: DbSession
) -> TreatmentPlanItem:
    plan = service.create_treatment_plan(
        db,
        user=user,
        cycle_id=body.cycle_id,
        input_id=body.input_id,
        dose=body.dose,
        operation_type=body.operation_type,
        planned_date=body.planned_date,
        planned_phase=body.planned_phase,
        basis_recommendation_id=body.basis_recommendation_id,
    )
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/treatment-plans/{plan.id}"
    row = _plan_row(db, plan)
    item = _plan_item(row)
    item.weather_window = _plan_window(db, row[1], plan)  # WEATHER-WINDOW, §7.2
    return item


@router.post(
    "/treatment-plans/{plan_id}/approve",
    response_model=TreatmentPlanItem,
    summary="API-16: согласование руководителем (draft → approved)",
)
def approve_treatment_plan(
    plan_id: uuid.UUID, user: ApproveAccess, db: DbSession
) -> TreatmentPlanItem:
    plan = service.get_treatment_plan(db, plan_id)
    plan = service.approve_treatment_plan(db, user=user, plan=plan)
    row = _plan_row(db, plan)
    item = _plan_item(row)
    item.weather_window = _plan_window(db, row[1], plan)  # WEATHER-WINDOW, §7.2
    return item
