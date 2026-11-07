"""API модуля спутника: API-06 — таймлайн NDVI по полю (задача MVP-10).

GET /fields/{field_id}/ndvi?index=&date_from=&date_to= (docs/06 §5):
временной ряд индексов (по умолчанию ndvi) с датами и cloud_pct, пагинация
CONTRACTS §8. Сцены с cloud_pct > 60 % в БД не попадают (05 §5), поэтому
в таймлайне их не бывает. meta.stale — нет свежей сцены в окне композита
14 суток (05 §2) при непустом источнике.

GET /fields/{field_id}/ndvi/{date}/render.png?variant=ndvi|truecolor
(docs/06 §7.3, #83): PNG-рендер сцены окна поля; заголовки X-Field-Bbox
(границы окна растра, WGS84) и X-Image-Size — для позиционирования
оверлея на мини-карте контура в карточке поля.

Права (07-ux §5, E-09): группа «satellite» — агроном, руководитель, админ.
"""

import json
import uuid
from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.models.agronomy import Field
from app.models.integrations import SatelliteScene
from app.models.references import Source
from app.modules.satellite.schemas import (
    INDICES,
    NdviTimelineResponse,
    SceneItem,
    SourceMeta,
)
from app.services.sentinel_ingest.renders import (
    RENDER_VARIANTS,
    meta_path,
    render_path,
    renders_exist,
)

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SATELLITE_SOURCE_ID = "satellite.sentinel2-l2a"
COMPOSITE_WINDOW_DAYS = 14  # окно композита (05 §2): нет сцены — stale

router = APIRouter(tags=["satellite"], dependencies=[Depends(group_checker("satellite"))])


def _get_field(db: Session, field_id: uuid.UUID) -> Field:
    field = db.get(Field, field_id)
    if field is None:
        raise AppError(
            f"Поле {field_id} не найдено",
            code="field_not_found",
            title="Не найдено",
            status_code=404,
        )
    return field


def _source_meta(db: Session, field_id: uuid.UUID, total: int) -> SourceMeta:
    source = db.scalar(select(Source).where(Source.source_id == SATELLITE_SOURCE_ID))
    stale = False
    if total > 0:
        newest = db.scalar(
            select(func.max(SatelliteScene.captured_at)).where(
                SatelliteScene.field_id == field_id, SatelliteScene.index == "ndvi"
            )
        )
        if newest is not None:
            stale = date.today() - newest > timedelta(days=COMPOSITE_WINDOW_DAYS)
    last_success = source.last_success_at if source else None
    if last_success is not None and last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=UTC)
    return SourceMeta(
        source_id=SATELLITE_SOURCE_ID,
        status=source.last_status if source else None,
        last_success_at=last_success,
        stale=stale,
    )


@router.get(
    "/fields/{field_id}/ndvi",
    response_model=NdviTimelineResponse,
    summary="API-06: таймлайн индексов по полю (NDVI по умолчанию)",
)
def field_ndvi(
    field_id: uuid.UUID,
    db: DbSession,
    *,
    index: str = Query(default="ndvi", description=f"Индекс: {', '.join(INDICES)}"),
    date_from: date | None = Query(default=None, description="Начало периода (включительно)"),
    date_to: date | None = Query(default=None, description="Конец периода (включительно)"),
    limit: int = Query(default=50, ge=1, le=366),
    offset: int = Query(default=0, ge=0),
) -> NdviTimelineResponse:
    field = _get_field(db, field_id)
    if index not in INDICES:
        raise AppError(
            f"Неизвестный индекс «{index}»; допустимо: {', '.join(INDICES)}",
            code="unknown_index",
            title="Некорректный запрос",
            status_code=422,
        )
    filters = [SatelliteScene.field_id == field.id, SatelliteScene.index == index]
    if date_from is not None:
        filters.append(SatelliteScene.captured_at >= date_from)
    if date_to is not None:
        filters.append(SatelliteScene.captured_at <= date_to)
    total = db.scalar(select(func.count()).select_from(SatelliteScene).where(*filters)) or 0
    scenes = list(
        db.scalars(
            select(SatelliteScene)
            .where(*filters)
            .order_by(SatelliteScene.captured_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    items = [
        SceneItem(
            scene_id=s.id,
            field_id=s.field_id,
            index=s.index,
            captured_at=s.captured_at,
            resolution_m=s.resolution_m,
            index_avg=float(s.index_avg),
            index_min=float(s.index_min),
            index_max=float(s.index_max),
            cloud_pct=float(s.cloud_pct),
            composite_days=s.composite_days,
            source_id=s.source_id,
            trust=float(s.trust),
            anomaly_zones_ref=s.anomaly_zones_ref,
        )
        for s in scenes
    ]
    return NdviTimelineResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        index=index,
        meta=_source_meta(db, field.id, total),
    )


__all__ = ["router"]
