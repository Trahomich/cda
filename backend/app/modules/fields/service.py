"""Сервис домена «поля»: контуры, версии, площадь, статусы (задача MVP-5).

Правила — docs/03-domain-agronomy.md §1.1 (ENT-FIELD, версионирование контуров,
REQ-F-002) и docs/06-data-api.md §2/§5. Площадь — геодезическая на эллипсоиде
WGS84 (03 §6.1): ST_Area(geography)/10000 га, хранится generated-колонкой
area_ga (миграция 0004). Геометрия приходит GeoJSON Polygon (CONTRACTS §5).

Ошибки — RFC 7807 (app.core.errors): 422 invalid_geometry / self_intersection /
area_below_min, 422 invalid_status_transition, 409 duplicate_name, 404 not_found.
"""

import json
import uuid
from datetime import UTC, date, datetime

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.agronomy import CropCycle, Field, FieldOperation, FieldVersion
from app.models.auth import AuditRecord, User
from app.models.references import Culture
from app.modules.geo import parse_geojson

# Минимальная площадь контура, га (03 §1.1: «площадь ≥ 0,1 га»)
MIN_AREA_GA = 0.1

# Статусы поля (03 §1.1 / dat_field): переходы только из active, терминальные —
# archived и merged (решение подтверждено в обсуждении задачи #23, пост #82)
FIELD_STATUSES = ("active", "merged", "archived")
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "active": {"archived", "merged"},
    "archived": set(),
    "merged": set(),
}

_GEOGRAPHY_POLYGON = Geography(geometry_type="POLYGON", srid=4326)


def _geom(geojson_str: str):
    """SQL-выражение geography(Polygon,4326) из GeoJSON-строки."""
    return cast(func.ST_GeomFromGeoJSON(geojson_str), _GEOGRAPHY_POLYGON)


def validate_contour_structure(contour: object) -> str:
    """Структурная валидация GeoJSON Polygon; возвращает JSON-строку.

    422 invalid_geometry: не Polygon, нет колец, кольцо < 4 точек,
    некорректные координаты, незамкнутое кольцо.
    """
    if not isinstance(contour, dict) or contour.get("type") != "Polygon":
        raise AppError(
            "Контур должен быть GeoJSON-объектом с type=Polygon (WGS84)",
            code="invalid_geometry",
            title="Некорректная геометрия",
            status_code=422,
        )
    rings = contour.get("coordinates")
    if not isinstance(rings, list) or not rings:
        raise AppError(
            "В contour.coordinates должен быть список колец (минимум внешнее)",
            code="invalid_geometry",
            title="Некорректная геометрия",
            status_code=422,
        )
    for num, ring in enumerate(rings, start=1):
        if not isinstance(ring, list) or len(ring) < 4:
            raise AppError(
                f"Кольцо {num}: нужно минимум 4 точки (замкнутое кольцо)",
                code="invalid_geometry",
                title="Некорректная геометрия",
                status_code=422,
            )
        for pos in ring:
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                raise AppError(
                    f"Кольцо {num}: каждая точка — [lon, lat]",
                    code="invalid_geometry",
                    title="Некорректная геометрия",
                    status_code=422,
                )
            if not all(_is_number(v) for v in pos[:2]):
                raise AppError(
                    f"Кольцо {num}: координаты должны быть числами",
                    code="invalid_geometry",
                    title="Некорректная геометрия",
                    status_code=422,
                )
        if list(ring[0])[:2] != list(ring[-1])[:2]:
            raise AppError(
                f"Кольцо {num} не замкнуто: первая и последняя точки должны совпадать",
                code="invalid_geometry",
                title="Некорректная геометрия",
                status_code=422,
            )
    return json.dumps(contour, ensure_ascii=False)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_geometry(db: Session, geojson_str: str) -> float:
    """Проверка геометрии в PostGIS и минимальной площади; возвращает площадь, га.

    422 self_intersection — самопересечение (ST_IsValidReason);
    422 invalid_geometry — прочая негодность геометрии;
    422 area_below_min — площадь < 0,1 га (03 §1.1).
    """
    try:
        reason, area_m2 = db.execute(
            select(
                func.ST_IsValidReason(func.ST_GeomFromGeoJSON(geojson_str)),
                func.ST_Area(func.ST_GeomFromGeoJSON(geojson_str).cast(_GEOGRAPHY_POLYGON)),
            )
        ).one()
    except Exception as exc:  # битый GeoJSON не парсится ST_GeomFromGeoJSON
        raise AppError(
            f"Геометрия не разобрана PostGIS: {exc}",
            code="invalid_geometry",
            title="Некорректная геометрия",
            status_code=422,
        ) from exc
    if reason != "Valid Geometry":
        code = "self_intersection" if "self-intersection" in reason.lower() else "invalid_geometry"
        raise AppError(
            f"Геометрия некорректна: {reason}",
            code=code,
            title="Некорректная геометрия",
            status_code=422,
        )
    area_ga = round(float(area_m2) / 10000.0, 2)
    if area_ga < MIN_AREA_GA:
        raise AppError(
            f"Площадь контура {area_ga} га меньше минимальной {MIN_AREA_GA} га",
            code="area_below_min",
            title="Некорректная геометрия",
            status_code=422,
        )
    return area_ga


def get_field(db: Session, field_id: uuid.UUID) -> Field:
    field = db.get(Field, field_id)
    if field is None:
        raise AppError(
            f"Поле {field_id} не найдено",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return field


def _audit(db: Session, *, user: User, entity_id: uuid.UUID, action: str, changed: dict) -> None:
    """Аудит критичного действия в dat_audit (06 §4)."""
    db.add(
        AuditRecord(
            entity="field", entity_id=entity_id, user_id=user.id, action=action, changed=changed
        )
    )


def _new_version(
    db: Session,
    field: Field,
    geojson_str: str,
    *,
    valid_from: date | None,
    reason: str | None,
) -> FieldVersion:
    """Новая версия контура (без commit — коммитит вызывающий).

    Поле переводится на новую версию: contour и current_version_id обновляются,
    прежние версии сохраняются (REQ-F-002).
    """
    max_no = db.scalar(
        select(func.max(FieldVersion.version_no)).where(FieldVersion.field_id == field.id)
    )
    version = FieldVersion(
        field_id=field.id,
        version_no=(max_no or 0) + 1,
        contour=_geom(geojson_str),
        valid_from=valid_from or date.today(),
        reason=reason,
    )
    db.add(version)
    db.flush()  # нужен id версии для current_version_id
    field.contour = _geom(geojson_str)
    field.current_version_id = version.id
    return version


def _check_name_free(db: Session, name: str, *, exclude_id: uuid.UUID | None = None) -> None:
    """409 duplicate_name: имена полей уникальны среди полей, созданных вручную.

    Ограничение uq_dat_field_name снято в v1-02 (в импортированном реальном
    хозяйстве имена повторяются), поэтому проверка выполняется в сервисе
    (API-02/API-03 сохраняют контракт 409 duplicate_name).
    """
    stmt = select(Field.id).where(Field.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Field.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise AppError(
            f"Поле с названием «{name}» уже существует",
            code="duplicate_name",
            title="Конфликт состояния",
            status_code=409,
        )


def create_field(db: Session, *, user: User, name: str, contour: dict, reason: str | None) -> Field:
    """API-02: создать поле с контуром (версия 1 создаётся автоматически)."""
    _check_name_free(db, name.strip())
    geojson_str = validate_contour_structure(contour)
    area_ga = check_geometry(db, geojson_str)
    field = Field(name=name.strip(), contour=_geom(geojson_str))
    db.add(field)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            f"Поле с названием «{name}» уже существует",
            code="duplicate_name",
            title="Конфликт состояния",
            status_code=409,
        ) from exc
    _new_version(db, field, geojson_str, valid_from=None, reason=reason or "создание поля")
    _audit(
        db,
        user=user,
        entity_id=field.id,
        action="create",
        changed={"name": field.name, "area_ga": area_ga},
    )
    db.commit()
    db.refresh(field)
    return field


def update_field(
    db: Session,
    field: Field,
    *,
    user: User,
    name: str | None = None,
    status: str | None = None,
    contour: dict | None = None,
    reason: str | None = None,
) -> Field:
    """API-03: правка поля. Смена контура создаёт новую версию; смена статуса —
    по автомату (active → archived/merged) с обязательным reason (аудит)."""
    changed = False
    if status is not None and status != field.status:
        if status not in FIELD_STATUSES:
            raise AppError(
                f"Неизвестный статус поля «{status}» (допустимо: {', '.join(FIELD_STATUSES)})",
                code="invalid_status",
                title="Некорректный запрос",
                status_code=400,
            )
        if status not in ALLOWED_TRANSITIONS[field.status]:
            raise AppError(
                f"Переход статуса {field.status} → {status} не разрешён "
                "(активен только active → archived и active → merged, 03 §1.1)",
                code="invalid_status_transition",
                title="Нарушено бизнес-правило",
                status_code=422,
            )
        if not reason:
            raise AppError(
                "Смена статуса поля требует reason (фиксируется в dat_audit)",
                code="reason_required",
                title="Некорректный запрос",
                status_code=400,
            )
        previous = field.status
        field.status = status
        _audit(
            db,
            user=user,
            entity_id=field.id,
            action="status_change",
            changed={"status_from": previous, "status_to": status, "reason": reason},
        )
        changed = True
    if name is not None and name != field.name:
        _check_name_free(db, name.strip(), exclude_id=field.id)
        previous_name = field.name
        field.name = name.strip()
        changed = True
        _audit(
            db,
            user=user,
            entity_id=field.id,
            action="update",
            changed={"name_from": previous_name, "name_to": field.name},
        )
    if contour is not None:
        geojson_str = validate_contour_structure(contour)
        area_ga = check_geometry(db, geojson_str)
        version = _new_version(db, field, geojson_str, valid_from=None, reason=reason)
        _audit(
            db,
            user=user,
            entity_id=field.id,
            action="update",
            changed={"new_version_no": version.version_no, "area_ga": area_ga, "reason": reason},
        )
        changed = True
    if not changed:
        return field
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            f"Поле с названием «{name}» уже существует",
            code="duplicate_name",
            title="Конфликт состояния",
            status_code=409,
        ) from exc
    db.refresh(field)
    return field


def add_version(
    db: Session,
    field: Field,
    *,
    user: User,
    contour: dict,
    valid_from: date | None,
    reason: str | None,
) -> FieldVersion:
    """POST /fields/{id}/versions: новая версия контура (REQ-F-002)."""
    geojson_str = validate_contour_structure(contour)
    area_ga = check_geometry(db, geojson_str)
    version = _new_version(db, field, geojson_str, valid_from=valid_from, reason=reason)
    _audit(
        db,
        user=user,
        entity_id=field.id,
        action="update",
        changed={"new_version_no": version.version_no, "area_ga": area_ga, "reason": reason},
    )
    db.commit()
    db.refresh(version)
    db.refresh(field)
    return version


def list_fields(
    db: Session,
    *,
    status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Field], int]:
    """API-01: список полей с фильтрами и пагинацией; возвращает (страница, всего)."""
    filters = []
    if status is not None:
        if status not in FIELD_STATUSES:
            raise AppError(
                f"Неизвестный статус «{status}» (допустимо: {', '.join(FIELD_STATUSES)})",
                code="invalid_status",
                title="Некорректный запрос",
                status_code=400,
            )
        filters.append(Field.status == status)
    if q:
        filters.append(Field.name.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(Field).where(*filters)) or 0
    fields = list(
        db.scalars(select(Field).where(*filters).order_by(Field.name).limit(limit).offset(offset))
    )
    return fields, int(total)


def current_cycles(db: Session, field_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Текущий (последний основной) цикл культуры по каждому полю (API-01).

    «Текущий» = последний завершённый/идущий год, не будущий плановый
    (v1-10: у полей есть циклы на годы вперёд); если циклов в прошлом
    нет — последний имеющийся (поле с планом только на будущее).
    """
    if not field_ids:
        return {}
    current_year = datetime.now(UTC).year
    rows = db.execute(
        select(
            CropCycle.field_id,
            CropCycle.year,
            CropCycle.kind,
            Culture.name,
            case((CropCycle.year <= current_year, 1), else_=0).label("is_past"),
        )
        .join(Culture, CropCycle.culture_id == Culture.id)
        .where(CropCycle.field_id.in_(field_ids), CropCycle.kind == "main")
        .order_by(
            CropCycle.field_id,
            text("is_past DESC"),
            CropCycle.year.desc(),
        )
    ).all()
    result: dict[uuid.UUID, dict] = {}
    for field_id, year, kind, culture_name, _is_past in rows:
        if field_id not in result:  # первый по (прошлое, год desc) — текущий
            result[field_id] = {"year": year, "kind": kind, "culture_name": culture_name}
    return result


def contour_geojson(db: Session, field: Field) -> dict:
    """Текущий контур поля как GeoJSON (ST_AsGeoJSON)."""
    raw = db.scalar(select(func.ST_AsGeoJSON(Field.contour)).where(Field.id == field.id))
    return parse_geojson(raw)


def version_geojson(db: Session, version: FieldVersion) -> dict:
    raw = db.scalar(
        select(func.ST_AsGeoJSON(FieldVersion.contour)).where(FieldVersion.id == version.id)
    )
    return parse_geojson(raw)


def list_versions(db: Session, field: Field) -> list[FieldVersion]:
    return list(
        db.scalars(
            select(FieldVersion)
            .where(FieldVersion.field_id == field.id)
            .order_by(FieldVersion.version_no)
        )
    )


def recent_operations(db: Session, field: Field, *, limit_count: int = 10) -> list[dict]:
    """Последние операции журнала для карточки поля (API-05)."""
    ops = db.scalars(
        select(FieldOperation)
        .where(FieldOperation.field_id == field.id)
        .order_by(FieldOperation.date.desc(), FieldOperation.created_at.desc())
        .limit(limit_count)
    )
    return [
        {
            "date": op.date,
            "operation_type": op.operation_type,
            "status": op.status,
            "performer": op.performer,
        }
        for op in ops
    ]
