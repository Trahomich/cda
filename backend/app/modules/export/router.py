"""API-32: экспорт данных хозяйства (задача MVP-14, REQ-NF-007).

GET /api/v1/export?entity=&format= (docs/06-data-api.md §5):
- entity — из белого списка MVP (fields, rotation_plans, crop_cycles, agro_inputs,
  treatment_plans, operations, machines, machine_jobs, alerts);
- format=json|csv (CSV: «;», UTF-8 с BOM, даты ISO 8601, GeoJSON-строка);
- отдача потоковая (StreamingResponse); роли — группа RBAC «export»
  (manager, admin, матрица 07-ux §5).
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.modules.export import service

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    prefix="/export", tags=["export"], dependencies=[Depends(group_checker("export"))]
)


@router.get("", summary="API-32: экспорт данных хозяйства (JSON/CSV)")
def export_data(
    entity: Annotated[str, Query(description="Сущность из белого списка MVP")],
    db: DbSession,
    format: str = Query(default="json", description="json | csv"),
) -> StreamingResponse:
    model = service.ENTITIES.get(entity)
    if model is None:
        raise AppError(
            f"Неизвестная сущность «{entity}»; допустимо: {', '.join(sorted(service.ENTITIES))}",
            code="unknown_entity",
            title="Некорректный запрос",
            status_code=400,
        )
    if format not in service.FORMATS:
        raise AppError(
            f"Неизвестный формат «{format}»; допустимо: {', '.join(service.FORMATS)}",
            code="invalid_format",
            title="Некорректный запрос",
            status_code=400,
        )
    total = service.count_rows(db, model)
    filename = f"export_{entity}_{date.today():%Y%m%d}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "csv":
        return StreamingResponse(
            service.iter_csv(db, model),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )
    return StreamingResponse(
        service.iter_json(db, model, total=total),
        media_type="application/json",
        headers=headers,
    )
