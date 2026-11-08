"""API «Выводов ассистента» (AI-01b): GET /fields/{id}/insights.

docs/06 §7.4, docs/09-ai §8.2 (задача #93): items — числовые детекторы
(всегда, синхронно < 200 мс), summary — LLM-сводка из кэша (фоновая
генерация, §8.1); source="rules" — Ollama недоступен/не настроен, UI
показывает только items. Права — как у API-06 (группа «satellite»,
07-ux §5 E-09: агроном, руководитель, админ). Ошибки — problem+json §5.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_assistant import advice, service
from app.ai_assistant.schemas import AdviceHistoryResponse, AdviceResponse, InsightsResponse
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.models.agronomy import Field
from app.models.ai import AIFieldAdvice

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

router = APIRouter(tags=["ai"], dependencies=[Depends(group_checker("satellite"))])


@router.get(
    "/fields/{field_id}/insights",
    response_model=InsightsResponse,
    summary="Выводы ассистента по полю (детекторы + LLM-сводка, API-51)",
)
def field_insights(
    field_id: uuid.UUID,
    background: BackgroundTasks,
    db: DbSession,
    settings: SettingsDep,
    *,
    year: int | None = Query(default=None, ge=2000, le=2100, description="Сезон YYYY (§7.5)"),
) -> InsightsResponse:
    field = db.get(Field, field_id)
    if field is None:
        raise AppError(
            f"Поле {field_id} не найдено",
            code="field_not_found",
            title="Не найдено",
            status_code=404,
        )
    return service.get_insights(db, field, background, settings, year=year)
