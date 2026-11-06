"""Pydantic-схемы журнала операций (задача MVP-9, docs/06-data-api.md §5,
API-17/18; сущности — docs/03-domain-agronomy.md §1.6/§1.7).

Бизнес-валидация (400/404/409/422 по RFC 7807) — в сервисе:
unknown_operation_type / unknown_operation_source, not_found (поле, цикл,
препарат, работа техники), duplicate_yield_record (UQ cycle+date+source),
yield_requires_harvesting / yield_requires_cycle.
"""

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

OPERATION_TYPES = ("spraying", "fertilizing", "seeding", "harvesting", "tillage", "other")
OPERATION_SOURCES = ("manual", "telemetry_import", "plan_execution")
YIELD_SOURCES = ("scales", "combine_sensor", "estimate")


class YieldRecordIn(BaseModel):
    """Урожайность внутри операции уборки (ENT-YIELD-RECORD, 03 §1.7)."""

    yield_c_ha: float = Field(gt=0, le=99999.9)
    gross_t: float | None = Field(default=None, ge=0, le=99999999.99)
    source: str = "scales"


class OperationCreate(BaseModel):
    """POST /operations (API-17): запись факта операции из офлайн-очереди PWA.

    client_id — uuid от клиента: повторная отправка того же client_id
    возвращает существующую операцию (200, без дубля).
    """

    client_id: uuid.UUID | None = None
    field_id: uuid.UUID
    cycle_id: uuid.UUID | None = None
    operation_type: str
    date: date
    input_id: uuid.UUID | None = None
    actual_dose: float | None = Field(default=None, ge=0, le=999999.99)
    actual_area_ga: float = Field(gt=0, le=999999.99)
    performer: str | None = Field(default=None, max_length=200)
    machine_job_id: uuid.UUID | None = None
    source: str = "manual"
    yield_record: YieldRecordIn | None = None


class YieldRecordItem(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    yield_c_ha: float
    gross_t: float | None
    source: str
    date: date


class OperationItem(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    operation_id: uuid.UUID
    field_id: uuid.UUID
    cycle_id: uuid.UUID | None
    operation_type: str
    date: date
    input_id: uuid.UUID | None
    actual_dose: float | None
    actual_area_ga: float
    performer: str | None
    machine_job_id: uuid.UUID | None
    client_id: uuid.UUID | None
    source: str
    deviation_flag: bool
    deviation_rationale: str | None
    needs_review: bool
    status: str
    yield_record: YieldRecordItem | None = None


class OperationListResponse(BaseModel):
    items: list[OperationItem]
    total: int
    limit: int
    offset: int
