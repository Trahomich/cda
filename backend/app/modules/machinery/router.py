"""API модуля техники: API-19..21 (задача MVP-7, docs/06-data-api.md §5).

Эндпоинты:
- API-19 GET /machines — парк (фильтры machine_type, status; пагинация);
- API-19 POST /machines — создание (госномер UQ → 409; width для захвата — 422);
- PATCH /machines/{id} — правка (статус по автомату 04 §1);
- DELETE /machines/{id} — списание (перевод в retired, терминальный статус);
- API-19 GET /machines/{id}/jobs — задания машины;
- API-20 GET /machine-jobs — все задания (фильтр status; пагинация);
- API-20 POST /machine-jobs — создание в статусе draft (RULE-TECH-04);
- API-20 PATCH /machine-jobs/{id} — draft → confirmed/rejected (прочее — 409);
- API-20 POST /machine-jobs/{id}/confirm — подтверждение (эквивалент PATCH);
- API-21 GET /maintenance, POST /maintenance — ТО и ремонты (без алертов).

Права (07-ux §5, задача MVP-7): чтение и задания — группа «machinery»
(агроном, руководитель, механизатор, админ); парк и ТО ведут manager/admin.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.rbac import group_checker, role_checker
from app.models.auth import User, UserRole
from app.models.machinery import Machine, MachineJob, Maintenance
from app.modules.machinery import service
from app.modules.machinery.schemas import (
    MaintenanceCreate,
    MaintenanceItem,
    MaintenanceListResponse,
    MachineJobCreate,
    MachineJobItem,
    MachineJobListResponse,
    MachineJobUpdate,
    MachineCreate,
    MachineItem,
    MachineListResponse,
    MachineUpdate,
)

DbSession = Annotated[Session, Depends(get_db)]
# парк и ТО ведут руководитель и админ (07-ux §5: E-18)
FleetWrite = Annotated[User, Depends(role_checker(UserRole.MANAGER, UserRole.ADMIN))]
# чтение и задания — группа «machinery» (агроном, руководитель, механизатор, админ)
MachineryRead = Annotated[User, Depends(group_checker("machinery"))]

machines_router = APIRouter(
    prefix="/machines", tags=["machinery"], dependencies=[Depends(group_checker("machinery"))]
)
machine_jobs_router = APIRouter(prefix="/machine-jobs", tags=["machinery"])
maintenance_router = APIRouter(
    prefix="/maintenance", tags=["machinery"], dependencies=[Depends(group_checker("machinery"))]
)

router = APIRouter()
router.include_router(machines_router)
router.include_router(machine_jobs_router)
router.include_router(maintenance_router)


def _machine_item(machine: Machine) -> MachineItem:
    return MachineItem(
        machine_id=machine.id,
        machine_type=machine.machine_type,
        model=machine.model,
        gov_number=machine.gov_number,
        width_m=float(machine.width_m) if machine.width_m is not None else None,
        tank_capacity_l=machine.tank_capacity_l,
        engine_hours=float(machine.engine_hours),
        status=machine.status,
    )


def _job_item(db: Session, job: MachineJob) -> MachineJobItem:
    machine = service.get_machine(db, job.machine_id)
    return MachineJobItem(
        job_id=job.id,
        machine_id=job.machine_id,
        machine_model=machine.model,
        field_operation_id=job.field_operation_id,
        performed_at=job.performed_at,
        area_ha=float(job.area_ha),
        fuel_l=float(job.fuel_l) if job.fuel_l is not None else None,
        track_ref=job.track_ref,
        duration_h=float(job.duration_h) if job.duration_h is not None else None,
        status=job.status,
        source=job.source,
    )


def _maintenance_item(item: Maintenance, machine_model: str) -> MaintenanceItem:
    return MaintenanceItem(
        maintenance_id=item.id,
        machine_id=item.machine_id,
        machine_model=machine_model,
        kind=item.kind,
        due_at_hours=float(item.due_at_hours),
        done_at=item.done_at,
        done_at_hours=float(item.done_at_hours) if item.done_at_hours is not None else None,
        downtime_h=float(item.downtime_h) if item.downtime_h is not None else None,
    )


@machines_router.get("", response_model=MachineListResponse, summary="API-19: парк техники")
def list_machines(
    db: DbSession,
    _: MachineryRead,  # noqa: ARG001 (роль проверена зависимостью)
    *,
    machine_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> MachineListResponse:
    machines, total = service.list_machines(
        db, machine_type=machine_type, status=status, limit=limit, offset=offset
    )
    return MachineListResponse(
        items=[_machine_item(m) for m in machines], total=total, limit=limit, offset=offset
    )


@machines_router.post(
    "", response_model=MachineItem, status_code=201, summary="API-19: добавить машину в парк"
)
def create_machine(
    body: MachineCreate,
    response: Response,
    user: FleetWrite,
    db: DbSession,
) -> MachineItem:
    machine = service.create_machine(db, user=user, data=body)
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/machines/{machine.id}"
    return _machine_item(machine)


@machines_router.get(
    "/{machine_id}", response_model=MachineItem, summary="API-19: карточка машины"
)
def get_machine(machine_id: uuid.UUID, db: DbSession) -> MachineItem:
    return _machine_item(service.get_machine(db, machine_id))


@machines_router.patch(
    "/{machine_id}", response_model=MachineItem, summary="Правка машины (статус — по автомату 04)"
)
def update_machine(
    machine_id: uuid.UUID, body: MachineUpdate, user: FleetWrite, db: DbSession
) -> MachineItem:
    machine = service.update_machine(db, service.get_machine(db, machine_id), user=user, data=body)
    return _machine_item(machine)


@machines_router.delete(
    "/{machine_id}", response_model=MachineItem, summary="Списание машины (→ retired)"
)
def retire_machine(machine_id: uuid.UUID, user: FleetWrite, db: DbSession) -> MachineItem:
    machine = service.update_machine(
        db,
        service.get_machine(db, machine_id),
        user=user,
        data=MachineUpdate(status="retired"),
    )
    return _machine_item(machine)


@machines_router.get(
    "/{machine_id}/jobs",
    response_model=MachineJobListResponse,
    summary="API-19: задания машины (смена)",
)
def list_machine_jobs(
    machine_id: uuid.UUID,
    db: DbSession,
    *,
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> MachineJobListResponse:
    machine = service.get_machine(db, machine_id)
    jobs, total = service.list_jobs(db, machine, status=status, limit=limit, offset=offset)
    return MachineJobListResponse(
        items=[_job_item(db, j) for j in jobs], total=total, limit=limit, offset=offset
    )


@machine_jobs_router.get(
    "", response_model=MachineJobListResponse, summary="API-20: все задания (фильтр status)"
)
def list_all_machine_jobs(
    db: DbSession,
    _: Annotated[User, Depends(group_checker("machinery"))],  # noqa: ARG001
    *,
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> MachineJobListResponse:
    jobs, total = service.list_all_jobs(db, status=status, limit=limit, offset=offset)
    return MachineJobListResponse(
        items=[_job_item(db, j) for j in jobs], total=total, limit=limit, offset=offset
    )


@machine_jobs_router.post(
    "",
    response_model=MachineJobItem,
    status_code=201,
    summary="API-20: создать задание (статус draft, RULE-TECH-04)",
)
def create_machine_job(
    body: MachineJobCreate,
    response: Response,
    user: Annotated[User, Depends(group_checker("machinery"))],
    db: DbSession,
) -> MachineJobItem:
    job = service.create_job(db, user=user, data=body)
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/machine-jobs/{job.id}"
    return _job_item(db, job)


@machine_jobs_router.patch(
    "/{job_id}",
    response_model=MachineJobItem,
    summary="API-20: смена статуса (draft → confirmed/rejected, прочее — 409)",
)
def update_machine_job(
    job_id: uuid.UUID, body: MachineJobUpdate, user: Annotated[User, Depends(group_checker("machinery"))], db: DbSession
) -> MachineJobItem:
    job = service.update_job(
        db,
        service.get_job(db, job_id),
        user=user,
        status=body.status,
        area_ha=body.area_ha,
        fuel_l=body.fuel_l,
        duration_h=body.duration_h,
    )
    return _job_item(db, job)


@machine_jobs_router.post(
    "/{job_id}/confirm",
    response_model=MachineJobItem,
    summary="API-20: подтверждение черновика человеком (RULE-TECH-04)",
)
def confirm_machine_job(
    job_id: uuid.UUID,
    user: Annotated[User, Depends(group_checker("machinery"))],
    db: DbSession,
) -> MachineJobItem:
    job = service.update_job(db, service.get_job(db, job_id), user=user, status="confirmed")
    return _job_item(db, job)


@maintenance_router.get(
    "", response_model=MaintenanceListResponse, summary="API-21: ТО и ремонты"
)
def list_maintenance_items(
    db: DbSession,
    _: MachineryRead,  # noqa: ARG001
    *,
    machine_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> MaintenanceListResponse:
    rows, total = service.list_maintenance(db, machine_id=machine_id, limit=limit, offset=offset)
    return MaintenanceListResponse(
        items=[_maintenance_item(item, machine.model) for item, machine in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@maintenance_router.post(
    "", response_model=MaintenanceItem, status_code=201, summary="API-21: план/факт ТО"
)
def create_maintenance_item(
    body: MaintenanceCreate,
    response: Response,
    user: FleetWrite,
    db: DbSession,
) -> MaintenanceItem:
    item = service.create_maintenance(db, user=user, data=body)
    machine = service.get_machine(db, item.machine_id)
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/maintenance/{item.id}"
    return _maintenance_item(item, machine.model)
