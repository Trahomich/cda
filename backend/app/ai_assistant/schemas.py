"""Схемы «Выводов ассистента» (AI-01b, docs/06 §7.4, 09-ai §8.2, задача #93)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

#: градация важности детектора (09-ai §8.2)
Severity = Literal["info", "warn", "alert"]

#: происхождение сводки: только числовые детекторы или детекторы + LLM
Source = Literal["rules", "rules+llm"]


class InsightItem(BaseModel):
    """Один числовой детектор: код, важность, заголовок, пояснение, ссылки.

    ``data_refs`` — ссылки на входные данные (формат 03 §1.5, как у
    рекомендаций): идентификаторы сцен/проб и источники измерений.
    """

    code: str
    severity: Severity
    title: str
    detail: str
    data_refs: list[dict[str, Any]] = []


class InsightsResponse(BaseModel):
    """Ответ GET /fields/{id}/insights (§7.4).

    ``items`` считаются всегда (без Ollama); ``summary`` — LLM-сводка
    (``null`` при недоступном Ollama или пока фоновая генерация не готова);
    ``source="rules"`` — сводки от LLM нет, UI показывает только items.
    """

    field_id: uuid.UUID
    generated_at: datetime
    source: Source
    summary: str | None
    items: list[InsightItem]


class AdviceResponse(BaseModel):
    """Одна AI-рекомендация «что делать сейчас» (06 §7.6, 09-ai §9.3)."""

    field_id: uuid.UUID
    created_at: datetime
    advice_date: date
    model: str
    provider: Literal["anthropic", "ollama"]
    answer: str
    status: Literal["ok", "fallback", "empty"]


class AdviceHistoryResponse(BaseModel):
    """Ответ GET /fields/{id}/ai/advice/history (API-56): только items."""

    items: list[AdviceResponse]


__all__ = [
    "AdviceHistoryResponse",
    "AdviceResponse",
    "InsightItem",
    "InsightsResponse",
    "Severity",
    "Source",
]
