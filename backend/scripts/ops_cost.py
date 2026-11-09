"""Стоимости работ на стенде: нормативы ref_operation_cost + cost операций (#162).

Слои:
1. Идемпотентное обновление справочника ref_operation_cost исследованными
   нормативами региона (docs/reports/operation-costs-2026.md, #162):
   tillage 1500, seeding 1200, fertilizing 900, spraying 700, harvesting 2000;
   other — 500 (уровень seed #161, в исследовании не переоценивался).
2. Проставление cost операциям журнала 2023–2026 (план = draft/planned/
   in_progress — норматив; факт = done — норматив × вариация U(0.85…1.15),
   детерминированная по id для воспроизводимости). cost_mode = 'unit'
   (руб/га), модель #161 (docs/06 §7.9). Бэкап изменённых строк JSON.

Режимы:
  python ops_cost.py [--dsn URL]              # только отчёт (что изменилось бы)
  python ops_cost.py --fix [--backup PATH]    # применить с бэкапом
  python ops_cost.py --restore PATH           # откат из бэкапа
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

#: исследованные нормативы региона, ₽/га (#162, docs/reports/operation-costs-2026.md)
RATE_RUB_GA: dict[str, float] = {
    "tillage": 1500.0,
    "seeding": 1200.0,
    "fertilizing": 900.0,
    "spraying": 700.0,
    "harvesting": 2000.0,
    "other": 500.0,  # уровень seed #161, не переоценивался
}

YEAR_FROM, YEAR_TO = 2023, 2026


def variation(op_id: str) -> float:
    """Детерминированная вариация факта 0.85…1.15 по id записи."""
    h = uuid.UUID(op_id).int % 1000
    return 0.85 + 0.30 * h / 999.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--backup", default=None)
    ap.add_argument("--restore", default=None)
    ap.add_argument("--control", default="45,46,50")
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
        rows = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        with engine.begin() as db:
            for r in rows:
                db.execute(
                    text(
                        "update dat_field_operation set cost_mode = :m, cost_value = :v "
                        "where id = :i"
                    ),
                    {"m": r["cost_mode"], "v": r["cost_value"], "i": r["id"]},
                )
        print(f"restore: {len(rows)} операций")
        return 0

    with engine.connect() as db:
        rates = dict(
            db.execute(text("select operation_type, rub_per_ga from ref_operation_cost")).all()
        )
        ops = (
            db.execute(
                text(
                    "select o.id, o.field_id, o.date, o.operation_type, o.status, "
                    "o.cost_mode, o.cost_value, f.name as field "
                    "from dat_field_operation o join dat_field f on f.id = o.field_id "
                    "where o.status <> 'cancelled' "
                    "and o.date >= :y1 and o.date <= :y2 order by o.field_id, o.date"
                ),
                {"y1": date(YEAR_FROM, 1, 1), "y2": date(YEAR_TO, 12, 31)},
            )
            .mappings()
            .all()
        )

    # 1) справочник: что изменится
    rate_changes = {t: (rates.get(t), v) for t, v in RATE_RUB_GA.items() if rates.get(t) != v}
    print(f"операций 2023–2026 (без cancelled): {len(ops)}")
    print(f"нормативы к обновлению: { {t: f'{old} → {new}' for t, (old, new) in rate_changes.items()} }")

    # 2) стоимости
    to_update = []
    for r in ops:
        base = RATE_RUB_GA.get(r["operation_type"])
        if base is None:
            continue
        value = base if r["status"] != "done" else round(base * variation(str(r["id"])), 2)
        if r["cost_mode"] == "unit" and float(r["cost_value"] or 0) == value:
            continue  # уже проставлено (идемпотентность)
        to_update.append({**dict(r), "new_value": value})

    by_type = Counter(r["operation_type"] for r in to_update)
    print(f"операций к проставлению cost: {len(to_update)}")
    for t, n in sorted(by_type.items()):
        print(f"  {t:12} ×{n} (норматив {RATE_RUB_GA[t]:.0f} ₽/га)")

    ctrl = {c for c in args.control.split(",")}
    print("\n-- контрольные поля (расчётные суммы факта, ₽) --")
    sum_by_field: dict[str, float] = {}
    for r in ops:
        base = RATE_RUB_GA.get(r["operation_type"])
        if base is None:
            continue
        v = r["cost_value"] if (r["cost_mode"] == "unit" and r["cost_value"]) else base * (
            variation(str(r["id"])) if r["status"] == "done" else 1.0
        )
        sum_by_field[str(r["field"])] = sum_by_field.get(str(r["field"]), 0.0) + float(v)
    for name, total in sorted(sum_by_field.items()):
        if name.split("/")[0] in ctrl:
            print(f"  {name}: Σфакт ≈ {total:,.0f} ₽")

    if args.fix:
        backup = []
        with engine.begin() as db:
            for t, (old, new) in rate_changes.items():
                db.execute(
                    text("update ref_operation_cost set rub_per_ga = :v where operation_type = :t"),
                    {"v": new, "t": t},
                )
            for r in to_update:
                row = dict(
                    db.execute(
                        text("select id, cost_mode, cost_value from dat_field_operation where id = :i"),
                        {"i": r["id"]},
                    ).mappings().first()
                )
                backup.append(row)
                db.execute(
                    text(
                        "update dat_field_operation set cost_mode = 'unit', "
                        "cost_value = :v where id = :i"
                    ),
                    {"v": r["new_value"], "i": r["id"]},
                )
        path = Path(args.backup or f"cost_backup_{date.today():%Y%m%d}.json")
        path.write_text(json.dumps(backup, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n--fix: нормативов обновлено {len(rate_changes)}, стоимостей проставлено "
              f"{len(to_update)}, бэкап: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
