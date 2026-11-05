"""Сервис домена «техника»: парк, задания смены, ТО (задача MVP-7).

Правила — docs/04-domain-farm.md §1 (ENT-MACHINE / ENT-MACHINE-JOB /
ENT-MAINTENANCE, RULE-TECH-04) и docs/06-data-api.md §5 (API-19..21).

Ошибки — RFC 7807 (app.core.errors):
  400 unknown_machine_type / unknown_job_status / unknown_maintenance_kind;
  404 not_found (машина, задание, операция);
  409 duplicate_gov_number, invalid_job_status_transition;
  422 width_required, invalid_status_transition, maintenance_hours_exceed_engine_hours.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.auth import AuditRecord, User
from app.models.machinery import Machine, MachineJob, Maintenance
from app.modules.machinery.schemas import (
    JOB_STATUSES,
    MACHINE_STATUSES,
    MACHINE_TYPES,
    WIDTH_REQUIRED_TYPES,
    MaintenanceCreate,
    MachineCreate,
    MachineJobCreate,
    MachineUpdate,
)

# Автомат статусов (04 §1): у машин retired — терминальный
MACHINE_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"maintenance", "retired"}),
    "maintenance": frozenset({"active", "retired"}),
    "retired": frozenset(),
}
# Автомат статусов задания (04 §1, RULE-TECH-04): подтверждает человек
JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"confirmed", "rejected"}),
    "confirmed": frozenset(),
    "rejected": frozenset(),
}


def _audit(db: Session, *, user: User, entity_id: uuid.UUID, action: str, changed: dict) -> None:
    """Аудит критичного действия в dat_audit (06 §4)."""
    db.add(
        AuditRecord(
            entity="machine", entity_id=entity_id, user_id=user.id, action=action, changed=changed
        )
    )


def get_machine(db: Session, machine_id: uuid.UUID) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise AppError(
            f"Машина {machine_id} не найдена",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return machine


def _check_machine_type(machine_type: str) -> None:
    if machine_type not in MACHINE_TYPES:
        raise AppError(
            f"Неизвестный тип техники «{machine_type}» "
            f"(допустимо: {', '.join(MACHINE_TYPES)})",
            code="unknown_machine_type",
        )


def _check_width(machine_type: str, width_m: float | None) -> None:
    """04 §1: width_m > 0 обязателен для техник с захватом (tractor/seeder/
    sprayer/spreader)."""
    if machine_type in WIDTH_REQUIRED_TYPES and not width_m:
        raise AppError(
            f"Для типа «{machine_type}» обязательна ширина захвата width_m > 0",
            code="width_required",
            title="Нарушено бизнес-правило",
            status_code=422,
        )


def list_machines(
    db: Session,
    *,
    machine_type: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Machine], int]:
    stmt = select(Machine).order_by(Machine.created_at, Machine.id)
    count_stmt = select(func.count()).select_from(Machine)
    if machine_type:
        stmt, count_stmt = stmt.where(Machine.machine_type == machine_type), count_stmt.where(
            Machine.machine_type == machine_type
        )
    if status:
        stmt, count_stmt = stmt.where(Machine.status == status), count_stmt.where(
            Machine.status == status
        )
    return list(db.scalars(stmt.limit(limit).offset(offset))), db.scalar(count_stmt) or 0


def create_machine(db: Session, *, user: User, data: MachineCreate) -> Machine:
    """API-19: POST /machines — госномер уникален (409 duplicate_gov_number)."""
    _check_machine_type(data.machine_type)
    if data.status not in MACHINE_STATUSES:
        raise AppError(
            f"Неизвестный статус техники «{data.status}» "
            f"(допустимо: {', '.join(MACHINE_STATUSES)})",
            code="unknown_machine_status",
        )
    _check_width(data.machine_type, data.width_m)
    machine = Machine(**data.model_dump())
    db.add(machine)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            f"Госномер «{data.gov_number}» уже зарегистрирован",
            code="duplicate_gov_number",
            title="Конфликт состояния",
            status_code=409,
        ) from exc
    _audit(
        db,
        user=user,
        entity_id=machine.id,
        action="create",
        changed={"model": machine.model, "gov_number": machine.gov_number},
    )
    db.commit()
    db.refresh(machine)
    return machine


def update_machine(
    db: Session, machine: Machine, *, user: User, data: MachineUpdate
) -> Machine:
    """PATCH /machines/{id}: смена статуса — по автомату (retired терминален)."""
    changes = data.model_dump(exclude_unset=True)
    if "machine_type" in changes and changes["machine_type"] is not None:
        _check_machine_type(changes["machine_type"])
        # тип меняется — захват перепроверяется на итоговом значении
    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if new_status not in MACHINE_STATUSES:
            raise AppError(
                f"Неизвестный статус техники «{new_status}» "
                f"(допустимо: {', '.join(MACHINE_STATUSES)})",
                code="unknown_machine_status",
            )
        if new_status != machine.status and new_status not in MACHINE_TRANSITIONS[machine.status]:
            raise AppError(
                f"Переход статуса техники «{machine.status}» → «{new_status}» невозможен",
                code="invalid_status_transition",
                title="Нарушено бизнес-правило",
                status_code=422,
            )
    final_type = changes.get("machine_type") or machine.machine_type
    final_width = changes["width_m"] if "width_m" in changes else machine.width_m
    _check_width(final_type, final_width)

    changed: dict = {}
    for key, value in changes.items():
        if value is None:
            continue
        old = getattr(machine, key)
        if old != value:
            setattr(machine, key, value)
            changed[key] = {"from": old, "to": value}
    if changed:
        action = "status_change" if "status" in changed and len(changed) == 1 else "update"
        _audit(db, user=user, entity_id=machine.id, action=action, changed=changed)
        db.commit()
        db.refresh(machine)
    return machine


def create_job(db: Session, *, user: User, data: MachineJobCreate) -> MachineJob:
    """POST /machine-jobs — задание создаётся в статусе draft (RULE-TECH-04).

    Связь с dat_field_operation (FK) — опциональна; если операция указана,
    она должна существовать (404), иначе задание останется без операции
    (внеполевые работы).
    """
    machine = get_machine(db, data.machine_id)
    if machine.status == "retired":
        raise AppError(
            f"Машина «{machine.model}» списана и не может выполнять работы",
            code="machine_retired",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    if data.source not in ("manual", "telemetry"):
        raise AppError(
            f"Неизвестный источник записи «{data.source}» (допустимо: manual, telemetry)",
            code="unknown_job_source",
        )
    job = MachineJob(status="draft", **data.model_dump())
    db.add(job)
    db.flush()  # проверка FK полей после вставки
    _audit(
        db,
        user=user,
        entity_id=job.id,
        action="job_create",
        changed={"machine": machine.model, "performed_at": str(job.performed_at)},
    )
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> MachineJob:
    job = db.get(MachineJob, job_id)
    if job is None:
        raise AppError(
            f"Задание {job_id} не найдено",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return job


def list_jobs(
    db: Session,
    machine: Machine,
    *,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[MachineJob], int]:
    stmt = select(MachineJob).where(MachineJob.machine_id == machine.id)
    stmt = stmt.order_by(MachineJob.performed_at.desc(), MachineJob.created_at.desc())
    count_stmt = (
        select(func.count()).select_from(MachineJob).where(MachineJob.machine_id == machine.id)
    )
    if status:
        if status not in JOB_STATUSES:
            raise AppError(
                f"Неизвестный статус задания «{status}» "
                f"(допустимо: {', '.join(JOB_STATUSES)})",
                code="unknown_job_status",
            )
        stmt = stmt.where(MachineJob.status == status)
        count_stmt = count_stmt.where(MachineJob.status == status)
    return list(db.scalars(stmt.limit(limit).offset(offset))), db.scalar(count_stmt) or 0


def update_job(db: Session, job: MachineJob, *, user: User, status: str, **fact) -> MachineJob:
    """PATCH /machine-jobs/{id} и API-20 confirm: автомат draft →
    confirmed/rejected (RULE-TECH-04); прочие переходы — 409."""
    if status not in JOB_STATUSES:
        raise AppError(
            f"Неизвестный статус задания «{status}» (допустимо: {', '.join(JOB_STATUSES)})",
            code="unknown_job_status",
        )
    if status not in JOB_TRANSITIONS[job.status]:
        raise AppError(
            f"Переход статуса задания «{job.status}» → «{status}» невозможен "
            "(draft → confirmed/rejected, 04 §1)",
            code="invalid_job_status_transition",
            title="Конфликт состояния",
            status_code=409,
        )
    job.status = status
    for key in ("area_ha", "fuel_l", "duration_h"):
        if fact.get(key) is not None:
            setattr(job, key, fact[key])
    _audit(
        db,
        user=user,
        entity_id=job.id,
        action=f"job_{status}",
        changed={"machine_id": str(job.machine_id), "status": status},
    )
    db.commit()
    db.refresh(job)
    return job


def create_maintenance(db: Session, *, user: User, data: MaintenanceCreate) -> Maintenance:
    """API-21: POST /maintenance — плановая наработка обязательна; факт
    (done_at/done_at_hours/downtime_h) заполняется после выполнения."""
    machine = get_machine(db, data.machine_id)
    if data.kind not in ("service", "repair", "inspection"):
        raise AppError(
            f"Неизвестный вид ТО «{data.kind}» (допустимо: service, repair, inspection)",
            code="unknown_maintenance_kind",
        )
    if data.done_at_hours is not None and data.done_at_hours > machine.engine_hours:
        raise AppError(
            f"Наработка на момент выполнения ({data.done_at_hours} м·ч) больше "
            f"текущей наработки машины «{machine.model}» ({machine.engine_hours} м·ч)",
            code="maintenance_hours_exceed_engine_hours",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    item = Maintenance(**data.model_dump())
    db.add(item)
    db.flush()
    _audit(
        db,
        user=user,
        entity_id=item.id,
        action="maintenance_create",
        changed={"machine": machine.model, "kind": item.kind, "due_at_hours": item.due_at_hours},
    )
    db.commit()
    db.refresh(item)
    return item


def list_maintenance(
    db: Session,
    *,
    machine_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[tuple[Maintenance, Machine]], int]:
    stmt = select(Maintenance, Machine).join(Machine, Maintenance.machine_id == Machine.id)
    stmt = stmt.order_by(Maintenance.due_at_hours, Maintenance.created_at)
    count_stmt = select(func.count()).select_from(Maintenance)
    if machine_id is not None:
        stmt = stmt.where(Maintenance.machine_id == machine_id)
        count_stmt = count_stmt.where(Maintenance.machine_id == machine_id)
    rows = list(db.execute(stmt.limit(limit).offset(offset)))
    return rows, db.scalar(count_stmt) or 0
