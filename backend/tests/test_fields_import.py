"""Тесты импорта реальных полей хозяйства из SHP (v1-02, IF-FIELDS).

Источник — docs/examples/agronet.fields.zip: 91 запись (90 полилиний +
1 запись без геометрии), проекция WGS84, кодировка utf-8. Проверяются
требования задачи #48:

- 90 полей создаётся, 1 запись без геометрии пропускается;
- геометрия в БД валидна (ST_IsValid) и полигонакальна (MultiPolygon);
- идемпотентность: повторный импорт обновляет (updated=90), дублей нет;
- расхождения площадей area_ha (атрибут) vs area_ga (геодезическая) > 5 %
  попадают в отчёт area_mismatch;
- replace=True удаляет прежние поля и импортирует заново;
- сумма площадей из геометрии близка к сумме атрибутов (~25784 га).
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.models.agronomy import Field, FieldVersion
from app.modules.fields.importer import SOURCE_ID, import_fields

# docs/examples/agronet.fields.zip — реальный файл хозяйства (задача #48)
BACKEND_ROOT = Path(__file__).resolve().parents[1]
SHP_ZIP = BACKEND_ROOT.parent / "docs" / "examples" / "agronet.fields.zip"

ATTR_AREA_TOTAL = 25784.2  # сумма area_ha по 90 записям источника
# допуск: геодезическая площадь vs «бумажная» атрибутная (самопересечения,
# огрехи, дискретизация контура) — до 5 % от суммы
AREA_TOTAL_TOLERANCE = 0.05

pytestmark = pytest.mark.skipif(
    not SHP_ZIP.exists(), reason=f"файл реального хозяйства не найден: {SHP_ZIP}"
)


def _import(db_session):
    return import_fields(db_session, str(SHP_ZIP))


def test_import_creates_90_fields(db_session):
    report = _import(db_session)
    assert report.created == 90
    assert report.updated == 0
    assert report.skipped == 1  # запись «ФФФ» без геометрии
    fields = db_session.scalars(select(Field)).all()
    assert len(fields) == 90
    # каждой созданной записи — версия контура №1 с источником импорта
    versions = db_session.scalars(select(FieldVersion)).all()
    assert len(versions) == 90
    assert all(v.version_no == 1 for v in versions)
    assert all(SOURCE_ID in (v.reason or "") for v in versions)
    assert {f.current_version_id for f in fields} == {v.id for v in versions}


def test_import_geometry_valid_multipolygon(db_session):
    _import(db_session)
    row = db_session.execute(
        text(
            "SELECT count(*),"
            " bool_and(ST_IsValid(contour::geometry)),"
            " bool_and(ST_GeometryType(contour::geometry) = 'ST_MultiPolygon')"
            " FROM dat_field"
        )
    ).one()
    total, all_valid, all_multi = row
    assert total == 90
    assert all_valid is True
    assert all_multi is True


def test_import_idempotent_by_external_id(db_session):
    first = _import(db_session)
    assert first.created == 90 and first.updated == 0
    second = _import(db_session)
    assert second.created == 0 and second.updated == 90 and second.skipped == 1
    assert db_session.scalar(select(func.count()).select_from(Field)) == 90
    # повторный импорт добавил по одной версии контура каждому полю
    assert db_session.scalar(select(func.count()).select_from(FieldVersion)) == 180
    max_no = db_session.execute(
        text("SELECT max(version_no) FROM dat_field_version")
    ).scalar_one()
    assert max_no == 2


def test_import_area_mismatch_report(db_session):
    report = _import(db_session)
    total = db_session.scalar(select(func.sum(Field.area_ga)))
    assert total is not None and total > 0
    # реальный источник содержит расхождения атрибутной и геометрической
    # площади; каждое расхождение отчёта — больше порога 5 %
    for item in report.area_mismatch:
        assert item["attr_ha"] > 0
        delta_pct = abs(item["calc_ha"] - item["attr_ha"]) / item["attr_ha"] * 100
        assert delta_pct > 5.0
        assert {"name", "attr_ha", "calc_ha"} == set(item)
    # сумма площадей из геометрии в пределах 5 % от суммы атрибутов
    assert abs(float(total) - ATTR_AREA_TOTAL) / ATTR_AREA_TOTAL < AREA_TOTAL_TOLERANCE


def test_import_replace_recreates_fields(db_session):
    _import(db_session)
    # между импортами ничего не меняем: replace должен удалить и создать заново
    report = import_fields(db_session, str(SHP_ZIP), replace=True)
    assert report.created == 90
    assert report.updated == 0
    assert any("replace" in w for w in report.warnings)
    assert db_session.scalar(select(func.count()).select_from(Field)) == 90
    # версии предыдущих полей удалены вместе с полями (каскад вручную)
    assert db_session.scalar(select(func.count()).select_from(FieldVersion)) == 90


def test_import_external_id_is_natural_key(db_session):
    """external_id обязателен для всех импортированных полей и уникален."""
    _import(db_session)
    rows = db_session.execute(
        select(Field.external_id, func.count()).group_by(Field.external_id)
    ).all()
    assert len(rows) == 90  # все external_id различны
    external_ids = {r[0] for r in rows}
    assert all(eid is not None for eid in external_ids)
    # коллизии name (дубликаты имён в источнике) разводятся суффиксами —
    # в реальном файле 5 имён повторяются (например, «51/ЖАМ/391га» ×4)
    names = db_session.scalars(select(Field.name)).all()
    assert len(set(names)) < len(names)
    # имена с коллизией получили external_id с суффиксом «#»
    assert any("#" in eid for eid in external_ids if eid)


def test_cli_import_fields(db_engine, monkeypatch, capsys):
    """Подкоманда CLI печатает отчёт JSON (контракт запуска v1-02).

    SessionLocal подменяется на тестовый engine, чтобы CLI не ушёл
    в БД из окружения (.env).
    """
    from sqlalchemy.orm import sessionmaker

    import app.cli as cli

    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=db_engine))
    rc = cli.main(["import-fields", str(SHP_ZIP)])
    assert rc == 0
    import json

    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["created"] == 90
    assert report["skipped"] == 1
    assert "area_mismatch" in report
