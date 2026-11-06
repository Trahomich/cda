"""Pydantic-схемы модуля алертов (API-27/28, MVP-12)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AlertItem(BaseModel):
    """Оповещение (ENT-ALERT, docs/03 §1.8): детали события — в payload."""

    model_config = ConfigDict(from_attributes=True)

    alert_id: uuid.UUID = Field(serialization_alias=None)
    type: str
    severity: str
    field_id: uuid.UUID | None
    user_id: uuid.UUID | None
    payload: dict
    status: str
    created_at: datetime
    updated_at: datetime


class AlertsListResponse(BaseModel):
    """Обёртка пагинации (CONTRACTS §8)."""

    items: list[AlertItem]
    total: int
    limit: int
    offset: int


class AlertPatch(BaseModel):
    """Переход статуса: new → read (взять в работу) → closed (закрыть)."""

    status: str


class JournalEventItem(BaseModel):
    """Последнее событие журнала адаптера (dat_source_journal)."""

    at: datetime
    status: str
    reason: str | None


class SourceStatusItem(BaseModel):
    """Состояние адаптера источника (REQ-F-034, экран E-14)."""

    source_id: str
    kind: str
    enabled: bool
    last_status: str | None
    last_success_at: datetime | None
    last_event: JournalEventItem | None


class SourcesStatusResponse(BaseModel):
    """Обёртка пагинации (CONTRACTS §8)."""

    items: list[SourceStatusItem]
    total: int
    limit: int
    offset: int


__all__ = [
    AlertItem,
    AlertPatch,
    AlertsListResponse,
    JournalEventItem,
    SourceStatusItem,
    SourcesStatusResponse,
]
