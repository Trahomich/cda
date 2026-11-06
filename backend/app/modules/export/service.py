"""Сервис экспорта доменных таблиц (задача MVP-14, API-32, REQ-NF-007).

Белый список сущностей — таблицы MVP (docs/06 §2): fields, rotation_plans,
crop_cycles, agro_inputs, treatment_plans, operations, machines, machine_jobs,
alerts. Строки отдаются потоково (yield_per) — большие таблицы не грузятся
в память целиком. Геометрия сериализуется в GeoJSON (ST_AsGeoJSON).
"""

import io
import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2.types import Geography, Geometry
from sqlalchemy import Select, func, inspect, select
from sqlalchemy.orm import Session

from app.models.agronomy import (
    AgroInput,
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    RotationPlan,
    TreatmentPlan,
)
from app.models.machinery import Machine, MachineJob
from app.modules.geo import unwrap_single_multi

# entity (API-32) -> ORM-модель; белый список доменных таблиц MVP
ENTITIES: dict[str, type] = {
    "fields": Field,
    "rotation_plans": RotationPlan,
    "crop_cycles": CropCycle,
    "agro_inputs": AgroInput,
    "treatment_plans": TreatmentPlan,
    "operations": FieldOperation,
    "machines": Machine,
    "machine_jobs": MachineJob,
    "alerts": Alert,
}

FORMATS = ("json", "csv")
CSV_DELIMITER = ";"
CHUNK_SIZE = 500  # строк за выборку при потоковой отдаче


def entity_select(model: Any) -> tuple[list[str], Select]:
    """Select всех колонок таблицы (в порядке схемы); геометрия → GeoJSON-строка."""
    keys: list[str] = []
    columns: list[Any] = []
    for col in inspect(model).columns:
        keys.append(col.key)
        if isinstance(col.type, (Geography, Geometry)):
            columns.append(func.ST_AsGeoJSON(col).label(col.key))
        else:
            columns.append(col)
    stmt = select(*columns).order_by(model.__table__.columns["id"])
    return keys, stmt


def count_rows(db: Session, model: type) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def csv_value(value: Any) -> Any:
    """Значение для CSV: null -> пусто, bool -> true/false, ISO 8601, Decimal как есть."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def json_value(value: Any) -> Any:
    """Значение для JSON: uuid/даты/Decimal → сериализуемые, GeoJSON-строка → объект."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and value.startswith('{"type"'):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict) and "type" in parsed:
                return unwrap_single_multi(parsed)
        except json.JSONDecodeError:
            pass
    return value


def _rows(db: Session, model: type) -> Iterator[tuple]:
    """Потоковое чтение строк (yield_per — курсор сервера, без полной выборки)."""
    _, stmt = entity_select(model)
    yield from db.execute(stmt.execution_options(yield_per=CHUNK_SIZE))


def iter_csv(db: Session, model: type) -> Iterator[str]:
    """CSV: разделитель «;», UTF-8 с BOM (Excel), даты ISO 8601, GeoJSON-строка."""
    keys, _ = entity_select(model)
    import csv as _csv

    buffer = io.StringIO()
    writer = _csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(keys)
    yield "﻿" + buffer.getvalue()  # BOM — Excel понимает UTF-8
    for row in _rows(db, model):
        buffer = io.StringIO()
        writer = _csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")
        writer.writerow([csv_value(v) for v in row])
        yield buffer.getvalue()


def iter_json(db: Session, model: type, *, total: int) -> Iterator[str]:
    """JSON в формате API-ответов: {"items": [...], "total", "limit", "offset"}."""
    keys, _ = entity_select(model)
    yield '{"items": ['
    first = True
    for row in _rows(db, model):
        obj = {k: json_value(v) for k, v in zip(keys, row, strict=True)}
        chunk = json.dumps(obj, ensure_ascii=False)
        yield chunk if first else ", " + chunk
        first = False
    yield f'], "total": {total}, "limit": {total}, "offset": 0}}'
