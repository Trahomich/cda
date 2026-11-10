"""Демо-данные МТП: справочник техники + привязки 1:N к плановым операциям (#181).

1. Справочник dat_machine: идемпотентный seed до ~19 единиц реалистичного
   парка хозяйства на 90 полей (тракторы, опрыскиватели, посевные
   комплексы/сеялки, разбрасыватель, комбайны; орудия-прицепы — жатки,
   культиваторы, борона, плуг — отдельные позиции, роль «прицеп»
   выражается trailer_id в dat_operation_machine).
2. Плановые операции 2023–2026: cost_plan (unit, норматив ref_operation_cost)
   тем, у кого нет; техника по смыслу типа (агрегат + прицеп через
   trailer_id, механизатор из пула, hours ≈ площадь × 0.06). Идемпотентно:
   привязка у операции уже есть / cost_plan уже задан — пропуск.

  python ops_machinery.py [--dsn URL] [--fix [--backup P]] [--restore P]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text

#: seed МТП: (machine_type, model, gov_number, width_m) — gov_number уникален
FLEET: list[tuple[str, str, str | None, float | None]] = [
    ("tractor", "Кировец К-744Р", "А345ВС 152", 0.0),
    ("tractor", "John Deere 8335R", "А412КМ 152", 0.0),
    ("tractor", "МТЗ-1221", "В201АА 152", 0.0),
    ("tractor", "К-700А (резерв)", "А098ОР 152", 0.0),
    ("sprayer", "Амазоне УФ-1801 (прицепной опрыскиватель)", None, 18.0),
    ("sprayer", "Харди Navigator 4000 (прицепной опрыскиватель)", None, 15.0),
    ("seeder", "Посевной комплекс ПК-12 «Террион» (прицепной)", None, 12.2),
    ("seeder", "Сеялка зерновая СЗ-5,4 (прицепная)", None, 5.4),
    ("spreader", "Разбрасыватель удобрений МВУ-5 (прицепной)", None, 10.0),
    ("combine", "Ростсельмаш Torum 580", "В777МН 152", 0.0),
    ("combine", "Ростсельмаш Acros 595", "В778МН 152", 0.0),
    ("combine", "Ростсельмаш Vector 410", "В779МН 152", 0.0),
    ("other", "Жатка зерновая ЖЗК-9 (прицеп)", None, 9.0),
    ("other", "Жатка зерновая PowerStream 9 (прицеп)", None, 9.0),
    ("other", "Жатка рапсовая SSR 9 (прицеп)", None, 9.0),
    ("other", "Культиватор KUHN Optimix (прицеп)", None, 6.0),
    ("other", "Культиватор КПС-4 (прицеп)", None, 4.0),
    ("other", "Борона дисковая БДМ 6×4 (прицеп)", None, 6.0),
    ("other", "Плуг ПГП-7-40 (прицеп)", None, 2.8),
]

#: пул механизаторов (демо)
OPERATORS = (
    "Иванов С. П.",
    "Петренко А. В.",
    "Сагинов Е. Н.",
    "Ковалёв Д. Ю.",
    "Абдрахманов Т. К.",
    "Мельник О. И.",
    "Дюсекенов Б. Е.",
    "Шмидт В. Р.",
)

#: агрегат + прицеп по типу операции: (machine_type агрегата, прицепное слово)
BY_OP: dict[str, tuple[str, str]] = {
    "seeding": ("seeder", None),  # трактор + сеялка
    "fertilizing": ("spreader", None),
    "spraying": ("sprayer", None),
    "harvesting": ("combine", "Жатка"),
    "tillage": ("tractor", None),  # прицеп-орудие отдельно
}
TILLAGE_TOOLS = ("Культиватор", "Борона", "Плуг")

HOURS_PER_GA = 0.06
YEAR_FROM, YEAR_TO = 2023, 2026


def h01(key: str) -> float:
    return (uuid.UUID(key).int % 10_000) / 9_999.0


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
            for m in data.get("machines", []):
                db.execute(text("delete from dat_machine where id = :i"), {"i": m})
            for om in data.get("links", []):
                db.execute(text("delete from dat_operation_machine where id = :i"), {"i": om})
            for c in data.get("costs", []):
                db.execute(
                    text(
                        "update dat_field_operation set cost_plan_mode = :m, "
                        "cost_plan_value = :v where id = :i"
                    ),
                    {"m": c["cost_plan_mode"], "v": c["cost_plan_value"], "i": c["id"]},
                )
        print(
            f"restore: машин удалено {len(data.get('machines', []))}, привязок "
            f"{len(data.get('links', []))}, стоимостей возвращено {len(data.get('costs', []))}"
        )
        return 0

    with engine.connect() as db:
        machines = (
            db.execute(text("select id, machine_type, model from dat_machine")).mappings().all()
        )
        rates = dict(
            db.execute(text("select operation_type, rub_per_ga from ref_operation_cost")).all()
        )
        plans = (
            db.execute(
                text(
                    "select o.id, o.field_id, o.operation_type, o.planned_date, o.date, "
                    "o.cost_plan_mode, o.cost_plan_value, f.area_ga "
                    "from dat_field_operation o join dat_field f on f.id = o.field_id "
                    "where o.status in ('draft', 'planned', 'in_progress') "
                    "and coalesce(o.planned_date, o.date) >= :y1 "
                    "and coalesce(o.planned_date, o.date) <= :y2"
                ),
                {"y1": date(YEAR_FROM, 1, 1), "y2": date(YEAR_TO, 12, 31)},
            )
            .mappings()
            .all()
        )
        linked = {
            r[0]
            for r in db.execute(text("select distinct operation_id from dat_operation_machine"))
        }

    existing_models = {m["model"] for m in machines}
    new_machines = [m for m in FLEET if m[1] not in existing_models]

    # индексы парка по типу/слову в модели
    by_type: dict[str, list] = {}
    for m in machines + [
        {"id": None, "machine_type": t, "model": n} for t, n, _, _ in new_machines
    ]:
        by_type.setdefault(m["machine_type"], []).append(m["model"])

    def pick(key: str, candidates: list[str]) -> str:
        return candidates[int(uuid.UUID(key).int) % len(candidates)]

    cost_updates: list[dict] = []
    links: list[dict] = []
    for p in plans:
        rate = rates.get(p["operation_type"])
        area = float(p["area_ga"] or 0.0)
        if rate is not None and p["cost_plan_value"] is None:
            cost_updates.append({"id": str(p["id"]), "v": float(rate)})
        if str(p["id"]) in linked:
            continue  # техника уже привязана (#172/#173)
        spec = BY_OP.get(p["operation_type"])
        if spec is None:
            continue
        unit_type, trailer_word = spec
        operator = pick(str(p["id"]) + "0", list(OPERATORS))
        hours = round(area * HOURS_PER_GA, 1)
        if p["operation_type"] in ("seeding", "fertilizing", "spraying"):
            tractor = pick(str(p["id"]) + "1", by_type["tractor"])
            unit = pick(str(p["id"]) + "2", by_type[unit_type])
            links.append(
                {
                    "op": str(p["id"]),
                    "machine": unit,
                    "trailer": None,
                    "operator": operator,
                    "hours": hours,
                    "tractor": tractor,
                }
            )
        elif p["operation_type"] == "harvesting":
            combine = pick(str(p["id"]) + "1", by_type["combine"])
            headers = [m for m in by_type.get("other", []) if "Жатка" in m]
            header = pick(str(p["id"]) + "2", headers) if headers else None
            links.append(
                {
                    "op": str(p["id"]),
                    "machine": combine,
                    "trailer": header,
                    "operator": operator,
                    "hours": hours,
                }
            )
        elif p["operation_type"] == "tillage":
            tractor = pick(str(p["id"]) + "1", by_type["tractor"])
            tools = [m for m in by_type.get("other", []) if any(t in m for t in TILLAGE_TOOLS)]
            tool = pick(str(p["id"]) + "2", tools) if tools else None
            links.append(
                {
                    "op": str(p["id"]),
                    "machine": tractor,
                    "trailer": tool,
                    "operator": operator,
                    "hours": hours,
                }
            )

    print(f"парк сейчас: {len(machines)}, к добавлению: {len(new_machines)}")
    print(f"планов 2023–2026: {len(plans)}; cost_plan к проставлению: {len(cost_updates)}")
    print(
        f"привязок техники к созданию: {len(links)} "
        f"({Counter(x['machine'].split()[0] for x in links).most_common(6)})"
    )

    if args.fix:
        created = {"machines": [], "links": [], "costs": []}
        with engine.begin() as db:
            model_to_id = {m["model"]: m["id"] for m in machines}
            for t, name, gov, width in new_machines:
                mid = uuid.uuid4()
                db.execute(
                    text(
                        "insert into dat_machine (id, machine_type, model, gov_number, "
                        "width_m, status) values (:i, :t, :m, :g, :w, 'active')"
                    ),
                    {"i": mid, "t": t, "m": name, "g": gov, "w": width},
                )
                model_to_id[name] = mid
                created["machines"].append(str(mid))
            for c in cost_updates:
                row = (
                    db.execute(
                        text(
                            "select id, cost_plan_mode, cost_plan_value from dat_field_operation "
                            "where id = :i"
                        ),
                        {"i": c["id"]},
                    )
                    .mappings()
                    .first()
                )
                created["costs"].append(dict(row))
                db.execute(
                    text(
                        "update dat_field_operation set cost_plan_mode = 'unit', "
                        "cost_plan_value = :v where id = :i"
                    ),
                    {"v": c["v"], "i": c["id"]},
                )
            for ln in links:
                oid = uuid.uuid4()
                db.execute(
                    text(
                        "insert into dat_operation_machine (id, operation_id, machine_id, "
                        "trailer_id, operator, hours) values (:i, :o, :m, :t, :op, :h)"
                    ),
                    {
                        "i": oid,
                        "o": ln["op"],
                        "m": model_to_id[ln["machine"]],
                        "t": model_to_id.get(ln["trailer"]) if ln.get("trailer") else None,
                        "op": ln["operator"],
                        "h": ln["hours"],
                    },
                )
                created["links"].append(str(oid))
        path = Path(args.backup or f"machinery_backup_{date.today():%Y%m%d}.json")
        path.write_text(json.dumps(created, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"--fix: машин добавлено {len(created['machines'])}, стоимостей "
            f"{len(created['costs'])}, привязок {len(created['links'])}; бэкап: {path}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
