"""Pydantic-схемы модуля техники (задача MVP-7, docs/06-data-api.md §5,
API-19..21; сущности — docs/04-domain-farm.md §1).

Бизнес-валидация (422/409/404 по RFC 7807) выполняется в сервисе:
width_required для техник с захватом, duplicate_gov_number, автомат
статусов задания (RULE-TECH-04: draft → confirmed/rejected).
"""

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

MACHINE_TYPES = (
    "tractor",
    "combine",
    "sprayer",
    "spreader",
    "seeder",
    "truck",
    "loader",
    "other",
)
MACHINE_STATUSES = ("active", "maintenance", "retired")
# типы, для которых обязателен width_m > 0 (04 §1 ENT-MACHINE)
WIDTH_REQUIRED_TYPES = frozenset({"tractor", "seeder", "sprayer", "spreader"})
JOB_STATUSES = ("draft", "confirmed", "rejected")
JOB_SOURCES = ("manual", "telemetry")
MAINTENANCE_KINDS = ("service", "repair", "inspection")


class MachineCreate(BaseModel):
    machine_type: str
    model: str = Field(min_length=1, max_length=200)
    gov_number: str | None = Field(default=None, max_length=20)
    width_m: float | None = Field(default=None, gt=0, le=999.99)
    tank_capacity_l: int | None = Field(default=None, ge=0)
    engine_hours: float = Field(default=0, ge=0, le=999999999)
    status: str = "active"


class MachineUpdate(BaseModel):
    """PATCH /machines/{id}: смена статуса — по автомату (retired терминален)."""

    machine_type: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    gov_number: str | None = Field(default=None, max_length=20)
    width_m: float | None = Field(default=None, gt=0, le=999.99)
    tank_capacity_l: int | None = Field(default=None, ge=0)
    engine_hours: float | None = Field(default=None, ge=0, le=999999999)
    status: str | None = None


class MachineItem(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    machine_id: uuid.UUID
    machine_type: str
    model: str
    gov_number: str | None
    width_m: float | None
    tank_capacity_l: int | None
    engine_hours: float
    status: str


class MachineListResponse(BaseModel):
    items: list[MachineItem]
    total: int
    limit: int
    offset: int


class MachineJobCreate(BaseModel):
    """POST /machine-jobs — создаёт задание в статусе draft (RULE-TECH-04)."""

    machine_id: uuid.UUID
    field_operation_id: uuid.UUID | None = None
    performed_at: date
    area_ha: float = Field(ge=0, le=999999.99)
    fuel_l: float | None = Field(default=None, ge=0, le=9999999.9)
    track_ref: str | None = Field(default=None, max_length=500)
    duration_h: float | None = Field(default=None, ge=0, le=999.99)
    source: str = "manual"


class MachineJobUpdate(BaseModel):
    """PATCH /machine-jobs/{id}: статус по автомату 04 §1 + корректировка факта."""

    status: str
    area_ha: float | None = Field(default=None, ge=0, le=999999.99)
    fuel_l: float | None = Field(default=None, ge=0, le=9999999.9)
    duration_h: float | None = Field(default=None, ge=0, le=999.99)


class MachineJobItem(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    job_id: uuid.UUID
    machine_id: uuid.UUID
    machine_model: str = Field(description="Марка/модель машины (развёрнуто для списка)")
    field_operation_id: uuid.UUID | None
    performed_at: date
    area_ha: float
    fuel_l: float | None
    track_ref: str | None
    duration_h: float | None
    status: str
    source: str


class MachineJobListResponse(BaseModel):
    items: list[MachineJobItem]
    total: int
    limit: int
    offset: int


class MaintenanceCreate(BaseModel):
    machine_id: uuid.UUID
    kind: str
    due_at_hours: float = Field(ge=0, le=999999999)
    done_at: date | None = None
    done_at_hours: float | None = Field(default=None, ge=0, le=999999999)
    downtime_h: float | None = Field(default=None, ge=0, le=9999.9)


class MaintenanceItem(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    maintenance_id: uuid.UUID
    machine_id: uuid.UUID
    machine_model: str = Field(description="Марка/модель машины (развёрнуто для списка)")
    kind: str
    due_at_hours: float
    done_at: date | None
    done_at_hours: float | None
    downtime_h: float | None


class MaintenanceListResponse(BaseModel):
    items: list[MaintenanceItem]
    total: int
    limit: int
    offset: int
