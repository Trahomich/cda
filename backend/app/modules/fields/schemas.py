"""Pydantic-схемы модуля полей (задача MVP-5, docs/06-data-api.md §5, API-01..05).

Контур — GeoJSON Polygon в WGS84 (CONTRACTS §5); структурная и геометрическая
валидация выполняется в сервисе (ошибки 422 invalid_geometry / self_intersection /
area_below_min — RFC 7807), поэтому в схемах contour — словарь без жёсткой формы.
"""

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field as PydField

FIELD_STATUSES = ("active", "merged", "archived")


class CurrentCycle(BaseModel):
    """Агрегат «текущий цикл культуры» в списке полей (API-01)."""

    year: int
    culture_name: str
    kind: str


class FieldItem(BaseModel):
    """Элемент списка полей (API-01); contour добавляется при with_geometry=true."""

    model_config = ConfigDict(serialize_by_alias=True)

    field_id: uuid.UUID
    name: str
    area_ga: float = PydField(description="Площадь, га (геодезическая, 03 §6.1)")
    status: str
    current_cycle: CurrentCycle | None = None
    contour: dict | None = PydField(default=None, description="GeoJSON Polygon (WGS84)")


class FieldListResponse(BaseModel):
    """Обёртка пагинации CONTRACTS §8: items / total / limit / offset."""

    items: list[FieldItem]
    total: int
    limit: int
    offset: int


class VersionItem(BaseModel):
    """Версия контура поля (ENT-FIELD.versions, 03 §1.1)."""

    version_no: int
    area_ga: float
    valid_from: date
    reason: str | None
    contour: dict


class VersionListResponse(BaseModel):
    items: list[VersionItem]
    total: int
    limit: int
    offset: int


class OperationBrief(BaseModel):
    """Операция журнала в карточке поля (API-05; полный журнал — API-18, задача MVP-9)."""

    date: date
    operation_type: str
    status: str
    performer: str | None


class FieldDetail(FieldItem):
    """Карточка «Поле 360°» (API-05, SC-01).

    ndvi и weather заполняются модулями спутника (MVP-10, #28) и погоды
    (MVP-6, #24); до их реализации возвращаются null.
    """

    contour: dict
    versions: list[VersionItem] = PydField(default_factory=list)
    operations: list[OperationBrief] = PydField(
        default_factory=list, description="Последние 10 операций (журнал)"
    )
    ndvi: dict | None = None
    weather: dict | None = None


class FieldCreate(BaseModel):
    """POST /fields (API-02)."""

    name: str = PydField(min_length=1, max_length=100)
    contour: dict = PydField(description="GeoJSON Polygon (WGS84, CONTRACTS §5)")
    reason: str | None = PydField(default=None, max_length=300)


class FieldUpdate(BaseModel):
    """PATCH /fields/{id} (API-03): смена контура создаёт новую версию.

    status — переход активного автомата (03 §1.1); для смены статуса
    обязателен reason (фиксируется в dat_audit).
    """

    name: str | None = PydField(default=None, min_length=1, max_length=100)
    status: str | None = None
    contour: dict | None = None
    reason: str | None = PydField(default=None, max_length=300)


class VersionCreate(BaseModel):
    """POST /fields/{id}/versions — новая версия контура (REQ-F-002)."""

    contour: dict
    valid_from: date | None = PydField(default=None, description="По умолчанию сегодня")
    reason: str | None = PydField(default=None, max_length=300)


class ImportErrorItem(BaseModel):
    """Ошибка одной строки импорта (ImportReport, docs/05 §IF-SOIL)."""

    row: int
    reason: str


class ImportReport(BaseModel):
    """Отчёт импорта: {created, skipped, errors} (docs/05, API-04)."""

    created: int
    skipped: int
    errors: list[ImportErrorItem]
