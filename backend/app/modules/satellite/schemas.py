"""Pydantic-схемы модуля спутника (API-06, docs/06-data-api.md §5; задача MVP-10)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.integrations.raster import INDICES


class SceneItem(BaseModel):
    """Элемент таймлайна индексов (ENT-SATELLITE-SCENE, 05 §3)."""

    scene_id: uuid.UUID
    field_id: uuid.UUID
    index: str
    captured_at: date
    resolution_m: int
    index_avg: float
    index_min: float
    index_max: float
    cloud_pct: float
    composite_days: int
    source_id: str
    trust: float
    anomaly_zones_ref: str | None = None


class SourceMeta(BaseModel):
    """Мета источника сцен (ref_source; stale — сцены старше окна композита)."""

    source_id: str
    status: str | None
    last_success_at: datetime | None
    stale: bool


class NdviTimelineResponse(BaseModel):
    """API-06: обёртка пагинации CONTRACTS §8 + мета источника."""

    items: list[SceneItem]
    total: int
    limit: int
    offset: int
    index: str
    meta: SourceMeta


__all__ = ["INDICES", "NdviTimelineResponse", "SceneItem", "SourceMeta"]
