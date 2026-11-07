"""Pydantic-схемы модуля спутника (API-06, docs/06-data-api.md §5; задача MVP-10)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

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
    # PNG-рендеры обоих вариантов на диске (docs/06 §7.3, #83)
    render_available: bool = False


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


class VraZoneItem(BaseModel):
    """Зона дифференцированного внесения (docs/06 §7.4)."""

    zone: int = Field(description="1 | 2 | 3")
    label: str = Field(description="пониженное | среднее | повышенное развитие")
    area_ga: float
    ndvi_mean: float
    dose_factor: float = Field(description="множитель дозы: 1.15 / 1.0 / 0.85")
    dose_kg_ha: float | None = Field(
        default=None, description="база × dose_factor; null — нет approved fertilizing-плана"
    )


class VraZonesResponse(BaseModel):
    """API-52: зоны дифференцированного внесения по последнему чистому NDVI."""

    field_id: uuid.UUID
    date: date = Field(description="дата сцены, по которой построены зоны")
    iqr_ndvi: float
    base_dose_kg_ha: float | None = None
    zones: list[VraZoneItem]
    map_png_url: str


__all__ = [
    "INDICES",
    "NdviTimelineResponse",
    "SceneItem",
    "SourceMeta",
    "VraZoneItem",
    "VraZonesResponse",
]
