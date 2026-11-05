"""API модуля полей: API-01..05 (задача MVP-5, docs/06-data-api.md §5).

Эндпоинты:
- API-01 GET /fields — список (фильтры status, q; пагинация; with_geometry);
- API-02 POST /fields — создание с контуром (201 + Location);
- API-03 PATCH /fields/{id} — правка (смена контура создаёт версию);
- API-04 POST /fields/import — импорт GeoJSON-файла, ImportReport;
- API-05 GET /fields/{id} — карточка «Поле 360°»;
- GET/POST /fields/{id}/versions — история и создание версий контура (REQ-F-002).

Права (07-ux §5): чтение — группа «fields» (агроном, руководитель, механизатор,
админ); изменение — agronomist/admin (задача MVP-5).
"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker, role_checker
from app.models.auth import User, UserRole
from app.modules.fields import service
from app.modules.fields.schemas import (
    FieldCreate,
    FieldDetail,
    FieldItem,
    FieldListResponse,
    FieldUpdate,
    ImportReport,
    ImportErrorItem,
    OperationBrief,
    VersionCreate,
    VersionItem,
    VersionListResponse,
)

DbSession = Depends(get_db)
# чтение — матрица 07-ux §5 (E-02/E-03); изменение — только агроном и админ
ReadAccess = Depends(group_checker("fields"))
WriteAccess = Depends(role_checker(UserRole.AGRONOMIST, UserRole.ADMIN))

router = APIRouter(
    prefix="/fields", tags=["fields"], dependencies=[ReadAccess], include_in_schema=True
)


def _item(
    db: Session, field, *, cycles: dict, with_geometry: bool = False
) -> FieldItem:
    cycle = cycles.get(field.id)
    return FieldItem(
        field_id=field.id,
        name=field.name,
        area_ga=float(field.area_ga),
        status=field.status,
        current_cycle=cycle,
        contour=service.contour_geojson(db, field) if with_geometry else None,
    )


def _detail(db: Session, field) -> FieldDetail:
    cycles = service.current_cycles(db, [field.id])
    versions = [
        VersionItem(
            version_no=v.version_no,
            area_ga=float(v.area_ga),
            valid_from=v.valid_from,
            reason=v.reason,
            contour=service.version_geojson(db, v),
        )
        for v in service.list_versions(db, field)
    ]
    return FieldDetail(
        field_id=field.id,
        name=field.name,
        area_ga=float(field.area_ga),
        status=field.status,
        current_cycle=cycles.get(field.id),
        contour=service.contour_geojson(db, field),
        versions=versions,
        operations=[
            OperationBrief(**op) for op in service.recent_operations(db, field)
        ],
        ndvi=None,  # заполняется модулем спутника (MVP-10, #28)
        weather=None,  # заполняется модулем погоды (MVP-6, #24)
    )


@router.get("", response_model=FieldListResponse, summary="API-01: список полей")
def list_fields(
    status: str | None = None,
    q: str | None = Query(default=None, max_length=100, description="Поиск по названию"),
    with_geometry: bool = Query(default=False, description="Добавить contour в элементы"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = DbSession,
) -> FieldListResponse:
    fields, total = service.list_fields(db, status=status, q=q, limit=limit, offset=offset)
    cycles = service.current_cycles(db, [f.id for f in fields])
    return FieldListResponse(
        items=[_item(db, f, cycles=cycles, with_geometry=with_geometry) for f in fields],
        total=total, limit=limit, offset=offset,
    )


@router.post("", response_model=FieldDetail, status_code=201,
             summary="API-02: создать поле с контуром")
def create_field(
    body: FieldCreate,
    response: Response,
    user: WriteAccess,  # noqa: ARG001 (роль проверена зависимостью)
    db: Session = DbSession,
) -> FieldDetail:
    field = service.create_field(
        db, user=user, name=body.name, contour=body.contour, reason=body.reason
    )
    response.headers["Location"] = f"{get_settings().api_v1_prefix}/fields/{field.id}"
    return _detail(db, field)


@router.get("/{field_id}", response_model=FieldDetail,
            summary="API-05: карточка поля («Поле 360°»)")
def get_field(field_id: uuid.UUID, db: Session = DbSession) -> FieldDetail:
    return _detail(db, service.get_field(db, field_id))


@router.patch("/{field_id}", response_model=FieldDetail,
              summary="API-03: правка поля (смена контура создаёт версию)")
def update_field(
    field_id: uuid.UUID,
    body: FieldUpdate,
    user: WriteAccess,
    db: Session = DbSession,
) -> FieldDetail:
    field = service.get_field(db, field_id)
    field = service.update_field(
        db, field, user=user, name=body.name, status=body.status,
        contour=body.contour, reason=body.reason,
    )
    return _detail(db, field)


@router.get("/{field_id}/versions", response_model=VersionListResponse,
            summary="История версий контура (REQ-F-002)")
def list_versions(field_id: uuid.UUID, db: Session = DbSession) -> VersionListResponse:
    field = service.get_field(db, field_id)
    versions = [
        VersionItem(
            version_no=v.version_no,
            area_ga=float(v.area_ga),
            valid_from=v.valid_from,
            reason=v.reason,
            contour=service.version_geojson(db, v),
        )
        for v in service.list_versions(db, field)
    ]
    return VersionListResponse(
        items=versions, total=len(versions), limit=len(versions), offset=0
    )


@router.post("/{field_id}/versions", response_model=VersionItem, status_code=201,
             summary="Новая версия контура (REQ-F-002)")
def add_version(
    field_id: uuid.UUID,
    body: VersionCreate,
    response: Response,
    user: WriteAccess,
    db: Session = DbSession,
) -> VersionItem:
    field = service.get_field(db, field_id)
    version = service.add_version(
        db, field, user=user, contour=body.contour,
        valid_from=body.valid_from, reason=body.reason,
    )
    response.headers["Location"] = (
        f"{get_settings().api_v1_prefix}/fields/{field.id}/versions/{version.version_no}"
    )
    return VersionItem(
        version_no=version.version_no,
        area_ga=float(version.area_ga),
        valid_from=version.valid_from,
        reason=version.reason,
        contour=service.version_geojson(db, version),
    )


@router.post("/import", response_model=ImportReport,
             summary="API-04: импорт полей из GeoJSON-файла (KML/shapefile — v1)")
async def import_fields(
    file: UploadFile,
    user: WriteAccess,
    db: Session = DbSession,
) -> ImportReport:
    """Импорт GeoJSON (FeatureCollection / Feature / Polygon).

    Отчёт ImportReport (docs/05 §IF-SOIL): created / skipped (дубли названия) /
    errors[{row, reason}] (нет properties.name, не Polygon, негодная геометрия).
    """
    raw = await file.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError(
            f"Файл не является корректным GeoJSON: {exc}",
            code="invalid_geojson",
            title="Некорректный запрос",
            status_code=422,
        ) from exc

    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif isinstance(data, dict) and data.get("type") == "Feature":
        features = [data]
    else:
        raise AppError(
            "Ожидался GeoJSON FeatureCollection или Feature",
            code="invalid_geojson",
            title="Некорректный запрос",
            status_code=422,
        )

    created = skipped = 0
    errors: list[ImportErrorItem] = []
    for row, feature in enumerate(features, start=1):
        props = feature.get("properties") or {}
        name = props.get("name")
        geometry = feature.get("geometry") or {}
        if not isinstance(name, str) or not name.strip():
            errors.append(ImportErrorItem(row=row, reason="нет properties.name"))
            continue
        if geometry.get("type") != "Polygon":
            errors.append(
                ImportErrorItem(
                    row=row, reason=f"тип геометрии {geometry.get('type')!r} не Polygon"
                )
            )
            continue
        try:
            service.create_field(
                db, user=user, name=name, contour=geometry, reason=f"импорт GeoJSON: {file.filename}"
            )
        except AppError as exc:
            if exc.code == "duplicate_name":
                skipped += 1
            else:
                errors.append(ImportErrorItem(row=row, reason=exc.detail))
        except (ValidationError, SQLAlchemyError) as exc:  # непредвиденное — в отчёт
            errors.append(ImportErrorItem(row=row, reason=str(exc)))
    return ImportReport(created=created, skipped=skipped, errors=errors)
