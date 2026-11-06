"""Pydantic-схемы модуля севооборота (задача MVP-8, docs/06 §5, API-08..11).

ENT-ROTATION-PLAN и ENT-CROP-CYCLE — docs/03 §1.2–1.3. items плана — циклы
поля в диапазоне лет (по одному main-циклу на год, RULE-AGRO-04); в POST
/rotation-plans items[] — необязательный черновик, валидируемый как dry-run
(нарушения возвращаются списком violations, записи не создаются).
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

PLAN_STATUSES = ("draft", "approved", "archived")
CYCLE_KINDS = ("main", "catch", "fallow")
CYCLE_STATUSES = ("planned", "in_progress", "completed")


class ViolationItem(BaseModel):
    """Нарушение бизнес-правила RULE-AGRO-* (rule_id/severity/rationale)."""

    rule_id: str
    severity: str = Field(description="ban — блок; warn — допустимо с reason")
    rationale: str


class PlanViolationItem(BaseModel):
    """Нарушение в on-demand пересчёте плана (API-08, docs/06 §7.2).

    Формат совпадает с violations POST /rotation-plans (rule_id/severity),
    дополнен cycle_year и detail=rationale."""

    rule_id: str
    severity: str
    cycle_year: int
    detail: str


class PlanViolationsResponse(BaseModel):
    """GET /rotation-plans/{id}/violations: пересчёт RULE-AGRO-01/03/04."""

    plan_id: uuid.UUID
    field_id: uuid.UUID
    checked_at: datetime
    items: list[PlanViolationItem]
    total: int


class CycleItem(BaseModel):
    """ENT-CROP-CYCLE в списках и карточках (docs/03 §1.3)."""

    cycle_id: uuid.UUID
    field_id: uuid.UUID
    year: int
    culture_id: uuid.UUID
    culture_name: str
    kind: str
    predecessor_cycle_id: uuid.UUID | None
    planned_yield_c_ha: float | None = None
    planned_harvest_date: date | None = None
    out_of_rotation: bool = False
    reason: str | None = None
    status: str


class CycleDetail(CycleItem):
    """Ответ POST /crop-cycles (API-11): цикл + предупреждения (severity=warn),
    пропущенные с комментарием агронома (reason)."""

    warnings: list[ViolationItem] = Field(default_factory=list)


class FieldCropCycleItem(BaseModel):
    """Элемент истории севооборота поля (API-49, v1-10 #66): цвет — для карты."""

    year: int
    culture_id: uuid.UUID
    culture_name: str
    kind: str
    status: str
    color_hex: str | None = Field(default=None, max_length=7)


class FieldCropCycleListResponse(BaseModel):
    """Обёртка API-49: items по убыванию года + total."""

    items: list[FieldCropCycleItem]
    total: int


class CycleCreate(BaseModel):
    """POST /crop-cycles (API-11). kind согласовывается с видом культуры
    (RULE-AGRO-03); reason обязателен при warn-нарушениях и out_of_rotation."""

    field_id: uuid.UUID
    year: int = Field(ge=2000, le=2100)
    culture_id: uuid.UUID
    kind: str
    planned_yield_c_ha: float | None = Field(default=None, gt=0, description="ц/га")
    planned_harvest_date: date | None = None
    out_of_rotation: bool = False
    reason: str | None = Field(default=None, max_length=300)


class PlanItemDraft(BaseModel):
    """Черновик цикла в items[] плана (dry-run: проверяется, не создаётся)."""

    year: int = Field(ge=2000, le=2100)
    culture_id: uuid.UUID
    kind: str
    planned_yield_c_ha: float | None = Field(default=None, gt=0, description="ц/га")
    out_of_rotation: bool = False
    reason: str | None = Field(default=None, max_length=300)


class PlanCreate(BaseModel):
    """POST /rotation-plans (API-09). Горизонт 2–10 лет (03 §1.2)."""

    field_id: uuid.UUID
    year_from: int = Field(ge=2000, le=2100)
    year_to: int = Field(ge=2000, le=2100)
    items: list[PlanItemDraft] = Field(
        default_factory=list, description="Черновик цепочки (dry-run, до 10 позиций)"
    )


class PlanDetail(BaseModel):
    """ENT-ROTATION-PLAN (docs/03 §1.2); items — циклы поля в диапазоне лет.

    violations заполняется только в ответе POST (dry-run items[])."""

    plan_id: uuid.UUID
    field_id: uuid.UUID
    year_from: int
    year_to: int
    status: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    items: list[CycleItem] = Field(default_factory=list)
    violations: list[ViolationItem] = Field(default_factory=list)


class PlanListResponse(BaseModel):
    """Обёртка пагинации CONTRACTS §8."""

    items: list[PlanDetail]
    total: int
    limit: int
    offset: int
