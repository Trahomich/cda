"""Ревью #172: проверка инвариантов §7.10 и идемпотентности переноса планов
на изолированной БД (мигранутой до head). Запуск из backend/:
DATABASE_URL_TEST=... .venv/bin/python ../.scratch/dmitriy.sokolov/check_172.py
"""
import os
import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

url = os.environ["DATABASE_URL_TEST"]
eng = create_engine(url)

with eng.connect() as c:
    c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    c.commit()
    # чистая схема под миграцию
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    c.commit()

import subprocess, sys

r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-2000:]
print("migrated to head OK")

with eng.begin() as c:
    # минимальные справочники: поле, цикл, культура, препарат, норматив, юзер
    c.execute(text("""
        INSERT INTO dat_field (id, external_id, name, status, area_ga, contour)
        VALUES (:f, 'EXT-REV', 'Ревью-поле', 'active', 100,
        ST_GeomFromGeoJSON('{"type":"Polygon","coordinates":[[[37.0,55.0],[37.1,55.0],[37.1,55.1],[37.0,55.1],[37.0,55.0]]]}'))
    """), {"f": str(uuid.uuid4())})
    c.execute(text("""
        INSERT INTO ref_culture (id, name, kind, is_archived)
        VALUES (:cl, 'Пшеница-ревью', 'main', false)
    """), {"cl": str(uuid.uuid4())})
    c.execute(text("""
        INSERT INTO dat_crop_cycle (id, field_id, year, culture_id, kind, status)
        SELECT :c, f.id, 2026, :cl, 'main', 'active' FROM dat_field f
    """), {"c": str(uuid.uuid4()), "cl": str(uuid.uuid4())})
    # фиксируем айдишники
    fid, cid = c.execute(text("SELECT f.id, c.id FROM dat_field f, dat_crop_cycle c LIMIT 1")).one()
    c.execute(text("""
        INSERT INTO dat_agro_input (id, name, input_type, unit, is_archived)
        VALUES (:i, 'Препарат-ревью', 'fertilizer', 'kg_ha', false)
    """), {"i": str(uuid.uuid4())})
    iid = c.execute(text("SELECT id FROM dat_agro_input LIMIT 1")).scalar_one()
    c.execute(text("""
        INSERT INTO ref_operation_cost (id, operation_type, rub_per_ga, valid_from)
        VALUES (:o, 'fertilizing', 700.0, DATE '2020-01-01')
    """), {"o": str(uuid.uuid4())})
    # планы внесения: approved с датой, completed с датой, без даты, cancelled
    c.execute(text("""
        INSERT INTO dat_treatment_plan
          (id, cycle_id, operation_type, input_id, dose, unit, planned_date, status, created_by)
        VALUES
          (:p1, :c, 'fertilizing', :i, 50, 'kg_ha', DATE '2026-05-10', 'approved', NULL),
          (:p2, :c, 'fertilizing', :i, 60, 'kg_ha', DATE '2026-05-20', 'completed', NULL),
          (:p3, :c, 'fertilizing', :i, 70, 'kg_ha', NULL, 'approved', NULL),
          (:p4, :c, 'fertilizing', :i, 80, 'kg_ha', DATE '2026-06-01', 'cancelled', NULL)
    """), {"p1": str(uuid.uuid4()), "p2": str(uuid.uuid4()),
           "p3": str(uuid.uuid4()), "p4": str(uuid.uuid4()),
           "c": cid, "i": iid})

# повторный запуск переноса планов: эмулируем повторное применение блока 5
transfer = text("""
INSERT INTO dat_field_operation (
    id, field_id, cycle_id, operation_type, date, planned_date,
    fact_date, input_id, input_dose, actual_area_ga, source,
    deviation_flag, needs_review, status, cost_plan_mode, cost_plan_value)
SELECT gen_random_uuid(), c.field_id, p.cycle_id, p.operation_type,
    COALESCE(p.planned_date, CURRENT_DATE), p.planned_date,
    CASE WHEN p.status='completed' THEN COALESCE(p.planned_date, CURRENT_DATE-3) END,
    p.input_id, p.dose, COALESCE(f.area_ga,0), 'plan_execution', false, false,
    CASE WHEN p.status='completed' THEN 'done'
         WHEN p.planned_date IS NULL THEN 'draft' ELSE 'planned' END,
    'unit', (SELECT o.rub_per_ga FROM ref_operation_cost o
             WHERE o.operation_type = p.operation_type)
FROM dat_treatment_plan p
JOIN dat_crop_cycle c ON c.id = p.cycle_id
JOIN dat_field f ON f.id = c.field_id
WHERE p.status <> 'cancelled'
  AND NOT EXISTS (SELECT 1 FROM dat_field_operation o
    WHERE o.cycle_id = p.cycle_id AND o.operation_type = p.operation_type
      AND o.input_id = p.input_id AND o.source = 'plan_execution'
      AND o.planned_date IS NOT DISTINCT FROM p.planned_date)
""")
with eng.begin() as c:
    c.execute(transfer)

with eng.connect() as c:
    rows = c.execute(text("""
        SELECT status, planned_date, fact_date IS NOT NULL, input_dose, cost_plan_value
        FROM dat_field_operation WHERE source='plan_execution' ORDER BY planned_date NULLS LAST
    """)).fetchall()
    print("перенесено:", rows)
    assert len(rows) == 3, f"ожидалось 3 операции, получено {len(rows)}"
    assert [r[0] for r in rows] == ["planned", "done", "draft"], rows
    assert rows[0][3] == 50 and float(rows[0][4]) == 700.0

# инвариант 1: done без fact_date — должен упасть
try:
    with eng.begin() as c:
        c.execute(text("""
            INSERT INTO dat_field_operation (id, field_id, cycle_id, operation_type, date,
              planned_date, actual_area_ga, source, status)
            VALUES (:id, :f, :c, 'spraying', DATE '2026-06-15', DATE '2026-06-15',
              10, 'manual', 'done')
        """), {"id": str(uuid.uuid4()), "f": fid, "c": cid})
    raise SystemExit("FAIL: done без fact_date прошёл")
except IntegrityError:
    print("OK: done без fact_date отклонён")

# инвариант 2: planned без planned_date — должен упасть
try:
    with eng.begin() as c:
        c.execute(text("""
            INSERT INTO dat_field_operation (id, field_id, cycle_id, operation_type, date,
              actual_area_ga, source, status)
            VALUES (:id, :f, :c, 'spraying', DATE '2026-06-16',
              10, 'manual', 'planned')
        """), {"id": str(uuid.uuid4()), "f": fid, "c": cid})
    raise SystemExit("FAIL: planned без дат прошёл")
except IntegrityError:
    print("OK: planned без planned_date отклонён")

# инвариант 3: fact_date при статусе planned — должен упасть
try:
    with eng.begin() as c:
        c.execute(text("""
            INSERT INTO dat_field_operation (id, field_id, cycle_id, operation_type, date,
              planned_date, fact_date, actual_area_ga, source, status)
            VALUES (:id, :f, :c, 'spraying', DATE '2026-06-17', DATE '2026-06-17',
              DATE '2026-06-17', 10, 'manual', 'planned')
        """), {"id": str(uuid.uuid4()), "f": fid, "c": cid})
    raise SystemExit("FAIL: fact при planned прошёл")
except IntegrityError:
    print("OK: fact_date при planned отклонён")

# откат миграции
r = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "-1"],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-2000:]
print("downgrade -1 OK")
r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-2000:]
print("upgrade head снова OK")
print("ALL CHECKS PASSED")
