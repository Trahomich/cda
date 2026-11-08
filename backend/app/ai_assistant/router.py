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


def _get_field_or_404(db: Session, field_id: uuid.UUID) -> Field:
    field = db.get(Field, field_id)
    if field is None:
        raise AppError(
            f"Поле {field_id} не найдено",
            code="field_not_found",
            title="Не найдено",
            status_code=404,
        )
    return field


@router.get(
    "/fields/{field_id}/ai/advice",
    response_model=AdviceResponse,
    summary="Последняя AI-рекомендация «что делать сейчас» (API-55)",
)
def field_advice(field_id: uuid.UUID, db: DbSession) -> AdviceResponse:
    _get_field_or_404(db, field_id)
    record = db.scalars(
        select(AIFieldAdvice)
        .where(AIFieldAdvice.field_id == field_id)
        .order_by(AIFieldAdvice.created_at.desc())
        .limit(1)
    ).first()
    if record is None:
        raise AppError(
            f"Для поля {field_id} ещё нет AI-рекомендации",
            code="advice_not_found",
            title="Не найдено",
            status_code=404,
        )
    return AdviceResponse.model_validate(record, from_attributes=True)


@router.post(
    "/fields/{field_id}/ai/advice",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Принудительный пересчёт AI-рекомендации в фоне (API-55)",
)
def field_advice_refresh(field_id: uuid.UUID, db: DbSession) -> dict[str, str]:
    _get_field_or_404(db, field_id)
    advice.queue_advice(field_id)
    return {"status": "queued"}


@router.get(
    "/fields/{field_id}/ai/advice/history",
    response_model=AdviceHistoryResponse,
    summary="История AI-рекомендаций поля, desc по created_at (API-56)",
)
def field_advice_history(
    field_id: uuid.UUID,
    db: DbSession,
    *,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> AdviceHistoryResponse:
    """История рекомендаций (§9.3); ``from``/``to`` — по ``advice_date``."""
    _get_field_or_404(db, field_id)
    stmt = select(AIFieldAdvice).where(AIFieldAdvice.field_id == field_id)
    if date_from is not None:
        stmt = stmt.where(AIFieldAdvice.advice_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(AIFieldAdvice.advice_date <= date_to)
    records = db.scalars(
        stmt.order_by(AIFieldAdvice.created_at.desc()).limit(limit)
    ).all()
    return AdviceHistoryResponse(
        items=[AdviceResponse.model_validate(r, from_attributes=True) for r in records]
    )
