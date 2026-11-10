"""Pydantic-схемы журнала операций (задача MVP-9, docs/06-data-api.md §5,
API-17/18; сущности — docs/03-domain-agronomy.md §1.6/§1.7).

Бизнес-валидация (400/404/409/422 по RFC 7807) — в сервисе:
unknown_operation_type / unknown_operation_source, not_found (поле, цикл,
препарат, работа техники), duplicate_yield_record (UQ cycle+date+source),
yield_requires_harvesting / yield_requires_cycle.
"""

import uuid
from datetime import date, datetime

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


class MachineLinkIn(BaseModel):
    """Машина операции (§7.10 dat_operation_machine): агрегат + прицеп опционально."""

    machine_id: uuid.UUID
    trailer_id: uuid.UUID | None = None
    operator: str | None = Field(default=None, max_length=200)
    hours: float | None = Field(default=None, ge=0, le=10000.0)


class MachineLinkItem(MachineLinkIn):
    """Машина операции в ответе: + имена для отображения."""

    machine_name: str
    trailer_name: str | None = None


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
    cost_rub: float | None = Field(
        default=None,
        description="Стоимость операции, руб: unit×площадь | total | "
        "норматив для факта (§7.9); null — не задана",
    )
    # -- Единая операция (§7.10, #173): план/факт, расходник, подтверждение --
    planned_date: date | None = Field(default=None, description="null — внеплановый факт")
    fact_date: date | None = Field(default=None, description="null — не выполнено")
    input_name: str | None = Field(default=None, description="Название расходника")
    input_dose: float | None = Field(default=None, description="Доза, кг/га (л/га)")
    input_amount_total: float | None = Field(default=None, description="Расход всего, кг/л")
    cost_plan_rub: float | None = Field(default=None, description="Стоимость ПО ПЛАНУ, руб")
    cost_fact_rub: float | None = Field(
        default=None, description="Стоимость ПО ФАКТУ, руб (норматив — при закрытии)"
    )
    confirmed_by: uuid.UUID | None = Field(default=None, description="Кто подтвердил (user)")
    confirmed_at: datetime | None = Field(default=None, description="Когда подтверждено")
    basis_recommendation_id: uuid.UUID | None = Field(
        default=None, description="Основание — ИИ-рекомендация"
    )
    machines: list[MachineLinkItem] = Field(
        default_factory=list, description="Техника операции (агрегат+прицеп), 1:N (§7.10)"
    )


class OperationListResponse(BaseModel):
    items: list[OperationItem]
    total: int
    limit: int
    offset: int


# -- Единая операция (§7.10, #173): план → факт → подтверждение, техника 1:N --


class UnifiedOperationPlanIn(BaseModel):
    """POST /operations — создание ПЛАНА (§7.10): planned_date ≠ null, факт пуст.

    input+доза опциональны (плановое внесение = операция с расходником);
    стоимость плана — unit (руб/га) | total (руб за поле), обе nullable.
    """

    field_id: uuid.UUID
    operation_type: str
    planned_date: date
    cycle_id: uuid.UUID | None = None
    input_id: uuid.UUID | None = None
    input_dose: float | None = Field(default=None, ge=0, le=999999.99)
    input_amount_total: float | None = Field(default=None, ge=0, le=99999999.99)
    cost_plan_mode: str | None = Field(default=None, pattern="^(unit|total)$")
    cost_plan_value: float | None = Field(default=None, ge=0, le=99999999.99)
    performer: str | None = Field(default=None, max_length=200)
    basis_recommendation_id: uuid.UUID | None = None


class UnifiedOperationPatchIn(BaseModel):
    """PATCH /operations/{id} — правка ПЛАНА до закрытия фактом (§7.10).

    После fact_date — 422 operation_already_done. Все поля опциональны.
    """

    planned_date: date | None = None
    input_id: uuid.UUID | None = None
    input_dose: float | None = Field(default=None, ge=0, le=999999.99)
    input_amount_total: float | None = Field(default=None, ge=0, le=99999999.99)
    cost_plan_mode: str | None = Field(default=None, pattern="^(unit|total)$")
    cost_plan_value: float | None = Field(default=None, ge=0, le=99999999.99)
    performer: str | None = Field(default=None, max_length=200)


class UnifiedOperationCompleteIn(BaseModel):
    """POST /operations/{id}/complete — закрытие фактом (§7.10).

    Расход всего: input_amount_total, иначе доза × площадь поля;
    стоимость факта: cost_fact (unit|total), иначе норматив ref_operation_cost.
    """

    fact_date: date
    input_amount_total: float | None = Field(default=None, ge=0, le=99999999.99)
    input_dose: float | None = Field(default=None, ge=0, le=999999.99)
    cost_fact_mode: str | None = Field(default=None, pattern="^(unit|total)$")
    cost_fact_value: float | None = Field(default=None, ge=0, le=99999999.99)
    performer: str | None = Field(default=None, max_length=200)
    deviation_flag: bool = False
    deviation_rationale: str | None = Field(default=None, max_length=500)
    machines: list[MachineLinkIn] = Field(default_factory=list)
    yield_record: YieldRecordIn | None = None
