"""Pydantic-схемы экономики сезона (API-62, §7.9, #161)."""

import uuid
from datetime import date as Date

from pydantic import BaseModel, Field


class OperationSide(BaseModel):
    """Сводка план/факт по типу операции: дата последней + Σ руб."""

    date: Date = Field(description="дата последней операции стороны")
    cost_rub: float = Field(description="сумма, руб; null-стоимости не входят")


class EconomicsOperationItem(BaseModel):
    """Строка детализации: план/факт по типу операции цикла."""

    type: str
    title: str
    plan: OperationSide | None = Field(
        default=None, description="null — факта без плана нет строки плана"
    )
    fact: OperationSide | None = Field(
        default=None, description="null — плановая операция не выполнена"
    )


class EconomicsResponse(BaseModel):
    """API-62: экономика сезона поля — стоимости техработ (§7.9)."""

    field_id: uuid.UUID
    year: int
    area_ga: float
    plan_cost_rub: float = Field(description="Σ плановых операций цикла, руб")
    fact_cost_rub: float = Field(description="Σ выполненных фактов, руб")
    cost_per_ga_rub: float = Field(description="факт / площадь поля, руб/га")
    fact_cost_per_ton_rub: float | None = Field(
        default=None,
        description="факт / тонны продукции (фактическая урожайность); null без неё",
    )
    yield_c_ha: float | None = Field(
        default=None, description="фактическая урожайность цикла, ц/га; null — нет"
    )
    operations: list[EconomicsOperationItem]


__all__ = [
    "EconomicsOperationItem",
    "EconomicsResponse",
    "OperationSide",
]
