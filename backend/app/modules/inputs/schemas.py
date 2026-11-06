"""Pydantic-схемы модуля препаратов (задача MVP-11, docs/06 §5, API-14..16).

ENT-AGRO-INPUT — docs/03 §1.4 (npk_content ↔ n_pct/p2o5_pct/k2o_pct БД),
ENT-TREATMENT-PLAN — docs/03 §1.5, статусы — автомат 03 §2.1. Список планов
несёт развёрнутые имена поля/цикла/препарата (экран E-05, frontend types.ts).
"""

import uuid
from datetime import date

from pydantic import BaseModel, Field

INPUT_KINDS = ("fertilizer", "ppp")
INPUT_UNITS = ("kg_ha", "l_ha", "kg_dv_ha")
OPERATION_TYPES = ("spraying", "fertilizing", "seed_treatment")
TREATMENT_STATUSES = ("draft", "approved", "in_progress", "completed", "cancelled")


class InputCreate(BaseModel):
    """POST /agro-inputs (API-14). npk_content — [% N, P2O5, K2O] для
    удобрений; active_substance/concentration/waiting_days — для СЗР."""

    kind: str = Field(description="fertilizer | ppp")
    name: str = Field(min_length=1, max_length=200)
    npk_content: list[float | None] | None = Field(
        default=None, min_length=3, max_length=3, description="[% N, P2O5, K2O]"
    )
    active_substance: str | None = Field(default=None, max_length=200)
    concentration: float | None = Field(default=None, ge=0, le=100)
    unit: str = Field(description="kg_ha | l_ha | kg_dv_ha")
    waiting_days: int | None = Field(
        default=None, ge=0, description="срок ожидания СЗР, дней (RULE-AGRO-08)"
    )
    approved_crops: list[uuid.UUID] = Field(
        default_factory=list, description="пусто = без ограничений (RULE-AGRO-10)"
    )
    max_treatments_per_season: int = Field(default=2, ge=1, description="RULE-AGRO-10")
    restrictions: str | None = None


class InputItem(BaseModel):
    """ENT-AGRO-INPUT (docs/03 §1.4)."""

    input_id: uuid.UUID
    kind: str
    name: str
    npk_content: list[float] | None = None
    active_substance: str | None = None
    concentration: float | None = None
    unit: str
    waiting_days: int | None = None
    approved_crops: list[uuid.UUID] = Field(default_factory=list)
    max_treatments_per_season: int
    restrictions: str | None = None


class InputListResponse(BaseModel):
    """Обёртка пагинации CONTRACTS §8."""

    items: list[InputItem]
    total: int
    limit: int
    offset: int


class TreatmentPlanCreate(BaseModel):
    """POST /treatment-plans (API-15). Проверки RULE-AGRO-07/08/10 — при
    создании и повторно при согласовании (API-16)."""

    cycle_id: uuid.UUID
    input_id: uuid.UUID
    dose: float = Field(description="в единицах input.unit; ≥ 0 (RULE-AGRO-07)")
    operation_type: str = Field(description="spraying | fertilizing | seed_treatment")
    planned_date: date
    planned_phase: str | None = Field(default=None, max_length=100)
    basis_recommendation_id: uuid.UUID | None = None


class WeatherWindow(BaseModel):
    """WEATHER-WINDOW (docs/06 §7.2) — подсказка для планов spraying.

    ok=false + detail («2026-06-10: ветер 6.2 м/с (>4), осадки 70 % (≥50)»),
    suggested_dates — до 5 подходящих дат из ближайших 7 суток. Не блокирует
    создание/согласование (жёсткие правила — только RULE-AGRO-07/08/10)."""

    ok: bool
    detail: str
    suggested_dates: list[date] = Field(default_factory=list)


class TreatmentPlanItem(BaseModel):
    """ENT-TREATMENT-PLAN (docs/03 §1.5) с развёрнутыми именами для списка
    E-05 (frontend/src/api/types.ts: TreatmentPlan). Для operation_type=
    spraying ответ POST (API-15) и approve (API-16) дополняется полем
    weather_window (WEATHER-WINDOW, §7.2): объект, либо null, если прогноза
    на planned_date в кэше нет; для прочих типов — null."""

    plan_id: uuid.UUID
    cycle_id: uuid.UUID
    field_id: uuid.UUID
    field_name: str
    year: int
    culture_name: str
    input_id: uuid.UUID
    input_name: str
    input_kind: str
    unit: str
    dose: float
    operation_type: str
    planned_date: date | None
    planned_phase: str | None
    status: str
    weather_window: WeatherWindow | None = None


class TreatmentPlanListResponse(BaseModel):
    """Обёртка пагинации CONTRACTS §8."""

    items: list[TreatmentPlanItem]
    total: int
    limit: int
    offset: int
