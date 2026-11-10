"""Плановые операции (план/факт пары) + фактические урожайности (#164).

1. Планы: для каждой done-операции циклов 2023–2025 создаётся плановая
   операция (status='planned', cycle_id тот же, дата = факт ±2–5 дней,
   cost_mode='unit', cost_value = норматив ref_operation_cost,
   actual_area_ga = площадь поля) — план≈факт, сезон закрыт. Для циклов
   2026: план по каждой операции цикла; циклам без фактов — плановые
   операции по техокнам культуры (tech-calendar-2026.json), они остаются
   незакрытыми («не выполнено», текущий сезон).
2. Урожайности: завершённым циклам (2023–2025 и убранным 2026) —
   dat_yield_record (source='scales', date = дата уборки факта или
   planned_harvest_date; если у цикла planned_yield_c_ha — факт = план
   ±10–20%, иначе диапазон по культуре ±20%; детерминированно по cycle_id).
   gross_t = yield/10 × площадь. Пар/пропашные-без-урожая не касаемся:
   пар пропускается.

Идемпотентно (у цикла уже есть планы/урожайность — пропуск), бэкап
созданных id JSON, --restore удаляет созданное.

  python ops_plan_yield.py [--dsn URL] [--json CAL] [--fix [--backup P]]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CAL = ROOT / "docs" / "reports" / "tech-calendar-2026.json"

#: плановая урожайность по культурам, ц/га (диапазон; #164)
YIELD_RANGES: dict[str, tuple[float, float]] = {
    "Озимая пшеница": (30, 45),
    "Рожь": (25, 35),
    "Тритикале": (30, 40),
    "Ячмень озимый": (30, 40),
    "Яровая пшеница": (20, 35),
    "Ячмень": (25, 35),
    "Овёс": (20, 30),
    "Гречиха": (12, 18),
    "Просо": (15, 25),
    "Кукуруза": (40, 70),
    "Кукуруза на силос": (250, 400),
    "Горох": (20, 30),
    "Чечевица": (12, 18),
    "Соя": (15, 25),
    "Подсолнечник": (15, 25),
    "Рапс": (15, 25),
    "Рапс озимый": (20, 30),
    "Люцерна": (35, 60),
    "Клевер с тимофеевкой": (30, 50),
    "Многолетние травы": (30, 50),
    "Однолетние травы": (25, 40),
    "Свёкла сахарная": (300, 500),
    "Картофель": (200, 350),
    "Овощи открытого грунта": (300, 600),
}

#: сдвиг плана от факта, дней (детерминированно по id)
PLAN_SHIFT_DAYS = (-5, -4, -3, -2, 2, 3, 4, 5)


def det01(key: str, salt: int = 0) -> float:
    """Детерминированное U(0,1) по uuid-строке."""
    return ((uuid.UUID(key).int + salt) % 10_000) / 9_999.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--json", default=str(DEFAULT_CAL))
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
            for y in data.get("yields", []):
                db.execute(text("delete from dat_yield_record where id = :i"), {"i": y})
            for p in data.get("plans", []):
                db.execute(text("delete from dat_field_operation where id = :i"), {"i": p})
        print(
            f"restore: удалено планов {len(data.get('plans', []))}, "
            f"урожайностей {len(data.get('yields', []))}"
        )
        return 0

    cal = json.loads(Path(args.json).read_text(encoding="utf-8"))
    # типовые даты операций группы: окно → середина
    group_windows: dict[str, dict] = {
        g: {
            op: (w["from"], w["to"])
            for op, w in gdata["windows"].items()
            if w.get("from") and w.get("to")
        }
        for g, gdata in cal["groups"].items()
    }
    culture_group: dict[str, str] = {
        name: gkey for gkey, gdata in cal["groups"].items() for name in gdata.get("cultures", [])
    }

    def mid_of(win: tuple[str, str], year: int) -> date:
        f = date(year, *map(int, win[0].split("-")))
        t = date(year, *map(int, win[1].split("-")))
        return f + (t - f) // 2

    with engine.connect() as db:
        rates = dict(
            db.execute(text("select operation_type, rub_per_ga from ref_operation_cost")).all()
        )
        cycles = (
            db.execute(
                text(
                    "select cc.id, cc.field_id, cc.year, cc.planned_yield_c_ha, "
                    "cc.planned_harvest_date, c.name as culture, f.area_ga "
                    "from dat_crop_cycle cc join ref_culture c on c.id = cc.culture_id "
                    "join dat_field f on f.id = cc.field_id "
                    "where cc.kind = 'main' and cc.year between 2021 and 2026 "
                    "order by cc.field_id, cc.year"
                )
            )
            .mappings()
            .all()
        )
        ops = (
            db.execute(
                text(
                    "select id, cycle_id, date, operation_type, status, actual_area_ga "
                    "from dat_field_operation where cycle_id is not null "
                    "and date >= '2021-01-01' and date <= '2026-12-31' "
                    "order by cycle_id, date"
                )
            )
            .mappings()
            .all()
        )
        has_plans = set(
            r[0]
            for r in db.execute(
                text(
                    "select distinct cycle_id from dat_field_operation "
                    "where status in ('draft','planned','in_progress')"
                )
            ).all()
        )
        has_yield = set(
            str(r[0])
            for r in db.execute(text("select distinct cycle_id from dat_yield_record")).all()
        )

    ops_by_cycle: dict = {}
    for r in ops:
        ops_by_cycle.setdefault(r["cycle_id"], []).append(r)

    new_plans: list[dict] = []
    new_yields: list[dict] = []
    for cy in cycles:
        if cy["year"] == 2026:
            continue  # второй проход ниже
        if str(cy["id"]) in has_plans and str(cy["id"]) not in ops_by_cycle.get(cy["id"], []):
            pass
        cycle_ops = ops_by_cycle.get(cy["id"], [])
        if not cycle_ops:
            continue
        if any(o["status"] in ("draft", "planned", "in_progress") for o in cycle_ops):
            continue  # идемпотентность: у цикла уже есть планы
        for o in cycle_ops:
            if o["status"] != "done":
                continue
            shift = PLAN_SHIFT_DAYS[int(uuid.UUID(str(o["id"])).int) % len(PLAN_SHIFT_DAYS)]
            rate = rates.get(o["operation_type"])
            if rate is None:
                continue
            new_plans.append(
                {
                    "cycle_id": str(cy["id"]),
                    "field_id": str(cy["field_id"]),
                    "date": (o["date"] + timedelta(days=shift)).isoformat(),
                    "operation_type": o["operation_type"],
                    "area": float(cy["area_ga"]),
                    "cost": float(rate),
                }
            )

    # циклы 2026: план для всех операций цикла (факт есть → пара; нет →
    # план по техокну культуры, останется незакрытым)
    for cy in cycles:
        if cy["year"] != 2026:
            continue
        cycle_ops = ops_by_cycle.get(cy["id"], [])
        if any(o["status"] in ("draft", "planned", "in_progress") for o in cycle_ops):
            continue
        group = culture_group.get(cy["culture"])
        windows = group_windows.get(group, {}) if group else {}
        planned_days: dict[str, date] = {}
        for o in cycle_ops:
            if o["status"] != "done":
                continue
            shift = PLAN_SHIFT_DAYS[int(uuid.UUID(str(o["id"])).int) % len(PLAN_SHIFT_DAYS)]
            planned_days.setdefault(o["operation_type"], o["date"] + timedelta(days=shift))
        # типы без факта — типовая дата из техокна (год цикла; озимый сев
        # относится к осени года цикла−1, но у 2026-циклов сев осенью 2025)
        for op_type, win in windows.items():
            if op_type in planned_days:
                continue
            year = (
                2025
                if (group in ("winter_cereals", "winter_oilseeds") and op_type == "seeding")
                else 2026
            )
            planned_days[op_type] = mid_of(win, year)
        for op_type, day in planned_days.items():
            rate = rates.get(op_type)
            if rate is None:
                continue
            new_plans.append(
                {
                    "cycle_id": str(cy["id"]),
                    "field_id": str(cy["field_id"]),
                    "date": day.isoformat(),
                    "operation_type": op_type,
                    "area": float(cy["area_ga"]),
                    "cost": float(rate),
                }
            )

    # урожайности: завершённые циклы = 2023–2025 + 2026 с фактом уборки
    # (2021–2022 не трогаем — #167 только плановые пары)
    for cy in cycles:
        if cy["year"] < 2023:
            continue
        if str(cy["id"]) in has_yield:
            continue
        if cy["culture"].startswith("Пар"):
            continue
        cycle_ops = ops_by_cycle.get(cy["id"], [])
        harvest_days = [
            o["date"]
            for o in cycle_ops
            if o["operation_type"] == "harvesting" and o["status"] == "done"
        ]
        finished = cy["year"] < 2026 or bool(harvest_days)
        if not finished:
            continue
        rng = YIELD_RANGES.get(cy["culture"])
        if rng is None:
            continue
        u = det01(str(cy["id"]), salt=7)
        if cy["planned_yield_c_ha"] is not None:
            value = float(cy["planned_yield_c_ha"]) * (0.8 + 0.4 * u)
        else:
            value = rng[0] + (rng[1] - rng[0]) * u
        value = max(1.0, round(value, 1))
        day = (max(harvest_days) if harvest_days else cy["planned_harvest_date"]) or date(
            cy["year"], 9, 15
        )
        area = float(cy["area_ga"] or 0.0)
        new_yields.append(
            {
                "cycle_id": str(cy["id"]),
                "yield_c_ha": value,
                "date": day.isoformat(),
                "gross_t": round(value / 10.0 * area, 2),
            }
        )

    print(f"циклов 2021–2026: {len(cycles)}, операций журнала: {len(ops)}")
    print(
        f"планов к созданию: {len(new_plans)} (2026-незакрытых в них: "
        f"{sum(1 for p in new_plans if p['date'] >= '2026-01-01')})"
    )
    print(f"урожайностей к созданию: {len(new_yields)}")

    if args.fix:
        created = {"plans": [], "yields": []}
        with engine.begin() as db:
            for p in new_plans:
                pid = uuid.uuid4()
                db.execute(
                    text(
                        "insert into dat_field_operation "
                        "(id, field_id, cycle_id, date, operation_type, status, "
                        "actual_area_ga, cost_mode, cost_value, source) "
                        "values (:id, :f, :c, :d, :t, 'planned', :a, 'unit', :v, 'manual')"
                    ),
                    {
                        "id": pid,
                        "f": p["field_id"],
                        "c": p["cycle_id"],
                        "d": p["date"],
                        "t": p["operation_type"],
                        "a": p["area"],
                        "v": p["cost"],
                    },
                )
                created["plans"].append(str(pid))
            for y in new_yields:
                yid = uuid.uuid4()
                db.execute(
                    text(
                        "insert into dat_yield_record "
                        "(id, cycle_id, yield_c_ha, gross_t, source, date) "
                        "values (:id, :c, :v, :g, 'scales', :d)"
                    ),
                    {
                        "id": yid,
                        "c": y["cycle_id"],
                        "v": y["yield_c_ha"],
                        "g": y["gross_t"],
                        "d": y["date"],
                    },
                )
                created["yields"].append(str(yid))
        path = Path(args.backup or f"plan_yield_backup_{date.today():%Y%m%d}.json")
        path.write_text(json.dumps(created, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"--fix: создано планов {len(created['plans'])}, урожайностей "
            f"{len(created['yields'])}; бэкап (для --restore): {path}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
