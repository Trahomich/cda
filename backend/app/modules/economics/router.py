"""API-62: экономика сезона поля (§7.9, #161).

Права — группа «operations» (журнал работ, 07-ux §5 E-13): суммы строятся
из операций журнала цикла.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.modules.economics.schemas import EconomicsResponse
from app.modules.economics.service import field_economics

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/fields", tags=["economics"], dependencies=[Depends(group_checker("operations"))]
)


@router.get(
    "/{field_id}/economics",
    response_model=EconomicsResponse,
    summary="API-62: экономика сезона — стоимость техработ план/факт (§7.9)",
)
def field_economics_api(
    field_id: uuid.UUID,
    db: DbSession,
    *,
    year: int | None = Query(
        default=None, ge=2000, le=2100, description="Сезон; по умолчанию текущий год"
    ),
) -> EconomicsResponse:
    from datetime import date

    season = year if year is not None else date.today().year
    try:
        data = field_economics(db, field_id, season)
    except LookupError as exc:
        raise AppError(
            str(exc), code="field_not_found", title="Не найдено", status_code=404
        ) from exc
    return EconomicsResponse(**data)
