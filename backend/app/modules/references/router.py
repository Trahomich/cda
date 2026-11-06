"""API модуля справочников: GET /references/cultures (v1-08, задача #54).

Справочник культур с color_hex — источник раскраски полей на карте (#55).
Чтение — группа «references» (E-15 матрицы прав 07-ux §5: А, Р, З, Д).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import group_checker
from app.models.references import Culture
from app.modules.references.schemas import CultureItem, CultureListResponse

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/references", tags=["references"], dependencies=[Depends(group_checker("references"))]
)


@router.get(
    "/cultures",
    response_model=CultureListResponse,
    summary="Справочник культур (name, kind, crop_group, repeat_years, color_hex)",
)
def list_cultures(
    db: DbSession,
    *,
    crop_group: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> CultureListResponse:
    stmt = select(Culture).order_by(Culture.name)
    if crop_group:
        stmt = stmt.where(Culture.crop_group == crop_group)
    if q:
        stmt = stmt.where(Culture.name.ilike(f"%{q}%"))
    items = db.scalars(stmt.limit(limit).offset(offset)).all()

    total_stmt = select(func.count()).select_from(Culture)
    if crop_group:
        total_stmt = total_stmt.where(Culture.crop_group == crop_group)
    if q:
        total_stmt = total_stmt.where(Culture.name.ilike(f"%{q}%"))
    total = db.scalar(total_stmt) or 0

    return CultureListResponse(
        items=[CultureItem.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )
