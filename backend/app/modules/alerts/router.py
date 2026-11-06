"""API модуля алертов: API-27 (оповещения) и API-28 (состояние адаптеров).

Эндпоинты (docs/06 §5):
- API-27 GET /alerts — список с фильтрами status/type/field_id, пагинация
  CONTRACTS §8; новые сверху;
- API-27 POST /alerts/{id}/read — пометить прочитанным (new → read);
- API-27 PATCH /alerts/{id} — переход статуса (new → read → closed),
  неверный переход — 409;
- API-28 GET /sources/status — ref_source (last_status/last_success_at)
  + последнее событие dat_source_journal, экран E-14.

Права (07-ux §5): «alerts» (E-13) — все роли; «sources» (E-14) — Р, Д.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.models.agronomy import Alert
from app.models.references import Source
from app.modules.alerts.schemas import (
    AlertItem,
    AlertPatch,
    AlertsListResponse,
    JournalEventItem,
    SourceStatusItem,
    SourcesStatusResponse,
)

DbSession = Annotated[Session, Depends(get_db)]

# автомат статусов ENT-ALERT (docs/03 §1.8): new → read → closed
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"read", "closed"}),
    "read": frozenset({"closed"}),
    "closed": frozenset(),
}

router = APIRouter(tags=["alerts"], dependencies=[Depends(group_checker("alerts"))])
sources_router = APIRouter(
    tags=["sources"], dependencies=[Depends(group_checker("sources"))]
)


def _to_item(alert: Alert) -> AlertItem:
    """ORM → схема API (числа/даты уже JSON-сериализуемы)."""
    return AlertItem(
        alert_id=alert.id,
        type=alert.type,
        severity=alert.severity,
        field_id=alert.field_id,
        user_id=alert.user_id,
        payload=alert.payload,
        status=alert.status,
        created_at=_aware(alert.created_at),
        updated_at=_aware(alert.updated_at),
    )


def _aware(value: datetime) -> datetime:
    """Наивное время БД → UTC (сервер может хранить без зоны)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _get_alert(db: Session, alert_id: UUID) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise AppError(
            f"Алерт {alert_id} не найден",
            code="alert_not_found",
            title="Не найдено",
            status_code=404,
        )
    return alert


def _set_status(db: Session, alert: Alert, new_status: str) -> Alert:
    """Переход статуса по автомату; неверный — 409 (docs/06 API-27)."""
    if new_status not in ALLOWED_TRANSITIONS:
        raise AppError(
            f"Неизвестный статус «{new_status}»; допустимо: new, read, closed",
            code="unknown_alert_status",
            title="Некорректный запрос",
            status_code=422,
        )
    if new_status == alert.status:
        return alert  # идемпотентно: повторный read уже прочитанного
    if new_status not in ALLOWED_TRANSITIONS[alert.status]:
        raise AppError(
            f"Недопустимый переход статуса {alert.status} → {new_status} "
            "(автомат: new → read → closed)",
            code="alert_invalid_transition",
            title="Конфликт состояний",
            status_code=409,
        )
    alert.status = new_status
    alert.updated_at = datetime.now(tz=UTC)
    db.commit()
    db.refresh(alert)
    return alert


@router.get("/alerts", response_model=AlertsListResponse, summary="API-27: оповещения")
def list_alerts(
    db: DbSession,
    *,
    status: str | None = Query(default=None, description="new / read / closed"),
    type: str = Query(default=None, description=f"Тип: {', '.join(sorted(ALERT_TYPES))}"),  # noqa: A002
    field_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AlertsListResponse:
    if status is not None and status not in ALLOWED_TRANSITIONS:
        raise AppError(
            f"Неизвестный статус «{status}»; допустимо: new, read, closed",
            code="unknown_alert_status",
            title="Некорректный запрос",
            status_code=422,
        )
    if type is not None and type not in ALERT_TYPES:
        raise AppError(
            f"Неизвестный тип алерта «{type}»; допустимо: {', '.join(sorted(ALERT_TYPES))}",
            code="unknown_alert_type",
            title="Некорректный запрос",
            status_code=422,
        )
    filters = []
    if status is not None:
        filters.append(Alert.status == status)
    if type is not None:
        filters.append(Alert.type == type)
    if field_id is not None:
        filters.append(Alert.field_id == field_id)
    total = db.scalar(select(func.count()).select_from(Alert).where(*filters)) or 0
    alerts = list(
        db.scalars(
            select(Alert)
            .where(*filters)
            .order_by(Alert.created_at.desc(), Alert.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return AlertsListResponse(
        items=[_to_item(a) for a in alerts],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/alerts/{alert_id}/read",
    response_model=AlertItem,
    summary="API-27: пометить прочитанным (new → read)",
)
def mark_read(alert_id: UUID, db: DbSession) -> AlertItem:
    alert = _get_alert(db, alert_id)
    alert = _set_status(db, alert, "read")
    return _to_item(alert)


@router.patch(
    "/alerts/{alert_id}",
    response_model=AlertItem,
    summary="API-27: сменить статус (new → read → closed)",
)
def patch_alert(alert_id: UUID, body: AlertPatch, db: DbSession) -> AlertItem:
    alert = _get_alert(db, alert_id)
    alert = _set_status(db, alert, body.status)
    return _to_item(alert)


@router.get(
    "/sources/status",
    response_model=SourcesStatusResponse,
    summary="API-28: состояние адаптеров источников",
)
def sources_status(
    db: DbSession,
    *,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SourcesStatusResponse:
    # последнее событие журнала на источник (DISTINCT ON — PostgreSQL)
    last_events: dict[str, JournalEventItem] = {}
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (source_id) source_id, at, status, reason "
            "FROM dat_source_journal ORDER BY source_id, at DESC"
        )
    ).fetchall()
    for row in rows:
        last_events[row[0]] = JournalEventItem(
            at=_aware(row[1]), status=row[2], reason=row[3]
        )
    total = db.scalar(select(func.count()).select_from(Source)) or 0
    sources = list(
        db.scalars(
            select(Source).order_by(Source.source_id).limit(limit).offset(offset)
        )
    )
    items = [
        SourceStatusItem(
            source_id=s.source_id,
            kind=s.kind,
            enabled=s.enabled,
            last_status=s.last_status,
            last_success_at=_aware(s.last_success_at)
            if s.last_success_at is not None
            else None,
            last_event=last_events.get(s.source_id),
        )
        for s in sources
    ]
    return SourcesStatusResponse(items=items, total=total, limit=limit, offset=offset)


__all__ = ["ALLOWED_TRANSITIONS", "router", "sources_router"]
