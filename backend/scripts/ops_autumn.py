"""Осенний блок работ 2026: озимый сев под 2027 + осенняя обработка почвы (#190).

1. 11 полей с озимым циклом 2027 (озимые культуры): tillage 05–20.09.2026
   (подготовка под сев, cycle 2027) + seeding озимых 20.09–05.10.2026
   (cycle 2027, год уборки) — done, стоимость факт = норматив ±15%
   (unit, детерминированно), техника по смыслу (трактор+плуг/культиватор,
   трактор+посевной комплекс), механизаторы из пула #181, часы = га × 0.06.
2. Прочие поля: осенняя обработка почвы (tillage) 05–25.10.2026 у ~2/3
   полей (детерминированно по field_id) — done, цикл 2026 (after-harvest),
   стоимость/техника по нормативам.
Даты только прошедшие (сегодня 2026-11-12). Идемпотентно (уже есть
осенний tillage/seeding у поля в 09–10.2026 → пропуск), бэкап id.

  python ops_autumn.py [--dsn URL] [--fix [--backup P]] [--restore P]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text

WINTER_MARKERS = ("озим", "рожь", "тритикале")
OPERATORS = (
    "Иванов С. П.", "Петренко А. В.", "Сагинов Е. Н.", "Ковалёв Д. Ю.",
    "Абдрахманов Т. К.", "Мельник О. И.", "Дюсекенов Б. Е.", "Шмидт В. Р.",
)
TILLAGE_TOOLS = ("Культиватор", "Борона", "Плуг")
SEEDERS = ("Посевной комплекс", "Сеялка")
HOURS_PER_GA = 0.06
TODAY = date(2026, 11, 12)  # guard: только прошедшие даты


def _h(key: str, salt: int = 0) -> int:
    return int.from_bytes(hashlib.md5(f"{key}{salt}".encode()).digest()[:8], "big")


def _date_in(lo: date, hi: date, key: str, salt: int) -> date:
    return lo + (hi - lo) * (_h(key, salt) % 1000) / 999


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--backup", default=None)
    ap.add_argument("--restore", default=None)
    args = ap.parse_args()

    dsn = args.dsn
    if not dsn:
        import os

        dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нет DSN", file=sys.stderr)
        return 2
    engine = create_engine(dsn)

    if args.restore:
        data = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        with engine.begin() as db:
            for om in data.get("links", []):
                db.execute(text("delete from dat_operation_machine where id = :i"), {"i": om})
            for op in data.get("ops", []):
                db.execute(text("delete from dat_field_operation where id = :i"), {"i": op})
        print(f"restore: операций удалено {len(data.get('ops', []))}, привязок "
              f"{len(data.get('links', []))}")
        return 0

    with engine.connect() as db:
        cycles27 = db.execute(text(
            "select cc.field_id, cc.id as cycle_id, c.name as culture, f.area_ga, f.name "
            "from dat_crop_cycle cc join ref_culture c on c.id = cc.culture_id "
            "join dat_field f on f.id = cc.field_id "
            "where cc.year = 2027 and cc.kind = 'main'"
        )).mappings().all()
        fields26 = dict(
            (r["field_id"], r["cycle_id"])
            for r in db.execute(text(
                "select cc.field_id, cc.id as cycle_id from dat_crop_cycle cc "
                "where cc.year = 2026 and cc.kind = 'main'"
            )).mappings().all()
        )
        rates = dict(db.execute(text(
            "select operation_type, rub_per_ga from ref_operation_cost")).all())
        machines = db.execute(text(
            "select id, machine_type, model from dat_machine"
        )).mappings().all()
        autumn = {
            (r["field_id"], r["operation_type"])
            for r in db.execute(text(
                "select field_id, operation_type from dat_field_operation "
                "where date between '2026-09-01' and '2026-10-31'"
            )).mappings().all()
        }

    by_model = {m["model"]: m["id"] for m in machines}
    tractors = [m["model"] for m in machines if m["machine_type"] == "tractor"]
    tools = [m["model"] for m in machines if any(t in m["model"] for t in TILLAGE_TOOLS)]
    seeders = [m["model"] for m in machines if any(s in m["model"] for s in SEEDERS)]

    def winter(culture: str) -> bool:
        low = culture.lower()
        return any(marker in low for marker in WINTER_MARKERS)

    def fact_cost(op_type: str, key: str) -> float:
        base = rates[op_type]
        return round(base * (0.85 + 0.30 * (_h(key, 9) % 1000) / 999), 2)

    def mk(field_id, cycle_id, op_type, day, key, machine_models):
        return {
            "field": str(field_id), "cycle": str(cycle_id), "op": op_type,
            "date": day.isoformat(), "cost": fact_cost(op_type, key),
            "machine": machine_models[0], "trailer": machine_models[1],
            "operator": OPERATORS[_h(key, 3) % len(OPERATORS)],
            "hours": None,  # заполним площадью ниже
        }

    new_ops: list[dict] = []
    winter_fields = set()
    for cy in cycles27:
        fid, area = cy["field_id"], float(cy["area_ga"] or 0.0)
        if winter(cy["culture"]):
            winter_fields.add(fid)
            key = str(cy["cycle_id"])
            if (fid, "tillage") not in autumn:
                day = _date_in(date(2026, 9, 5), date(2026, 9, 20), key, 1)
                op = mk(fid, cy["cycle_id"], "tillage", day, key + "t",
                        (tractors[_h(key, 11) % len(tractors)],
                         tools[_h(key, 12) % len(tools)] if tools else None))
                op["hours"] = round(area * HOURS_PER_GA, 1)
                new_ops.append(op)
            if (fid, "seeding") not in autumn:
                day = _date_in(date(2026, 9, 20), date(2026, 10, 5), key, 2)
                op = mk(fid, cy["cycle_id"], "seeding", day, key + "s",
                        (tractors[_h(key, 21) % len(tractors)],
                         seeders[_h(key, 22) % len(seeders)] if seeders else None))
                op["hours"] = round(area * HOURS_PER_GA, 1)
                new_ops.append(op)

    n_tillage_other = 0
    for fid, cycle26 in fields26.items():
        if fid in winter_fields:
            continue
        if (fid, "tillage") in autumn:
            continue
        if _h(str(fid), 7) % 1000 >= 667:  # ~1/3 полей без осенней обработки
            continue
        area_rows = [cy for cy in cycles27 if cy["field_id"] == fid]
        area = float(area_rows[0]["area_ga"]) if area_rows else 0.0
        key = str(cycle26)
        day = _date_in(date(2026, 10, 5), date(2026, 10, 25), key, 5)
        op = mk(fid, cycle26, "tillage", day, key + "a",
                (tractors[_h(key, 31) % len(tractors)],
                 tools[_h(key, 32) % len(tools)] if tools else None))
        op["hours"] = round(area * HOURS_PER_GA, 1)
        new_ops.append(op)
        n_tillage_other += 1

    print(f"озимых полей 2027: {len(winter_fields)}; к созданию операций: {len(new_ops)} "
          f"(озимые tillage+seeding: {sum(1 for o in new_ops if o['cycle'] != str(fields26.get(uuid.UUID(o['field']))) )}, "
          f"осенний tillage прочих: {n_tillage_other})")
    assert all(date.fromisoformat(o["date"]) <= TODAY for o in new_ops), "будущие даты!"

    if args.fix:
        created = {"ops": [], "links": []}
        with engine.begin() as db:
            for o in new_ops:
                oid = uuid.uuid4()
                db.execute(
                    text(
                        "insert into dat_field_operation "
                        "(id, field_id, cycle_id, date, planned_date, fact_date, "
                        "operation_type, status, actual_area_ga, source, "
                        "cost_fact_mode, cost_fact_value) "
                        "values (:i, :f, :c, :d, :d, :d, :t, 'done', "
                        "(select area_ga from dat_field where id = :f), 'manual', "
                        "'unit', :v)"
                    ),
                    {"i": oid, "f": o["field"], "c": o["cycle"], "d": o["date"],
                     "t": o["op"], "v": o["cost"]},
                )
                created["ops"].append(str(oid))
                if o["machine"]:
                    lid = uuid.uuid4()
                    db.execute(
                        text(
                            "insert into dat_operation_machine "
                            "(id, operation_id, machine_id, trailer_id, operator, hours) "
                            "values (:i, :o, :m, :t, :op, :h)"
                        ),
                        {"i": lid, "o": oid, "m": by_model[o["machine"]],
                         "t": by_model.get(o["trailer"]) if o["trailer"] else None,
                         "op": o["operator"], "h": o["hours"]},
                    )
                    created["links"].append(str(lid))
        path = Path(args.backup or "autumn_backup_20261112.json")
        path.write_text(
            json.dumps(created, default=str, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"--fix: создано операций {len(created['ops'])}, привязок "
              f"{len(created['links'])}; бэкап: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
