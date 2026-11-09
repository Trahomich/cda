"""Сверка и разовая актуализация работ по техокнам культур (#158).

Читает docs/reports/tech-calendar-2026.json (группа культур × операция × окно
от–до, MM-DD) и проверяет по каждому полю:

- dat_field_operation (журнал): операция относится к циклу поля по правилу
  «год цикла = год уборки» (месяц ≤ 7 → цикл year(D); месяц ≥ 9 → сначала
  year(D)+1, затем year(D); август — оба кандидата; берётся существующий
  main-цикл) — как current_culture_name §8.2;
- dat_treatment_plan (планы): цикл известен по cycle_id.

Режимы:
  python ops_check.py [--dsn URL] [--json PATH]          # только отчёт
  python ops_check.py --fix [--backup PATH]              # правки + бэкап

Правила правок (--fix):
- дата вне окна операции → сдвиг в ближайшую границу окна целевого года
  (для озимого сева — осень cycle.year−1, остальные — cycle.year);
- вегетационная операция (seeding/spraying/harvesting) по циклу «Пар» →
  замена типа на tillage (дата — в окно tillage); fertilizing по пару
  легален только 20.08–30.10 (основное внесение), иначе сдвиг туда;
- seed_treatment (планы) проверяется по окну seeding группы.

Выход: человекочитаемый отчёт (stdout) + при --fix бэкап исходных строк JSON.
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

ROOT = Path(__file__).resolve().parent.parent.parent  # корень репо
DEFAULT_JSON = ROOT / "docs" / "reports" / "tech-calendar-2026.json"

MONTH_DAY_FMT = "%m-%d"


def parse_md(value: str) -> tuple[int, int]:
    m, d = value.split("-")
    return int(m), int(d)


#: допуск на границах окна: ±10 дней — погода года, не нарушение (#158 §3)
TOLERANCE_DAYS = 10


def _doy(m: int, d: int) -> int:
    return (date(2001, m, d) - date(2001, 1, 1)).days


def in_window(day: date, w_from: tuple[int, int], w_to: tuple[int, int]) -> bool:
    """Дата в окне (с допуском TOLERANCE_DAYS за каждой границей)."""
    cur, lo, hi = _doy(day.month, day.day), _doy(*w_from), _doy(*w_to)
    return lo - TOLERANCE_DAYS <= cur <= hi + TOLERANCE_DAYS


def clamp_to_window(day: date, w_from: tuple[int, int], w_to: tuple[int, int], year: int) -> date:
    """Ближайшая дата окна year: clamp месяца-дня без пересечения года."""
    low = date(year, w_from[0], w_from[1])
    high = date(year, w_to[0], w_to[1])
    if day < low:
        return low
    if day > high:
        return high
    return day


def build_maps(cal: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """name → {op: (from,to)} и name → group_key (по спискам cultures)."""
    per_culture: dict[str, dict] = {}
    group_of: dict[str, str] = {}
    for gkey, g in cal["groups"].items():
        for name in g.get("cultures", []):
            windows = {}
            for op, w in g["windows"].items():
                if w.get("from") and w.get("to"):
                    windows[op] = (parse_md(w["from"]), parse_md(w["to"]))
            per_culture[name] = windows
            group_of[name] = gkey
    # сидерат (занятый пар)
    gm = cal["groups"]["fallow"].get("green_manure", {})
    for name in gm.get("cultures", []):
        per_culture[name] = {
            op: (parse_md(w["from"]), parse_md(w["to"])) for op, w in gm.items() if op != "cultures"
        }
        group_of[name] = "fallow"
    return per_culture, group_of


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None, help="default: env DATABASE_URL")
    ap.add_argument("--json", default=str(DEFAULT_JSON))
    ap.add_argument("--fix", action="store_true", help="применить правки (иначе только отчёт)")
    ap.add_argument("--backup", default=None, help="куда писать бэкап (с --fix)")
    ap.add_argument(
        "--control", default="45,46,50", help="контрольные номера полей для детализации"
    )
    ap.add_argument(
        "--restore",
        default=None,
        help="восстановить строки из бэкапа --fix (JSON) и выйти",
    )
    args = ap.parse_args()

    cal = json.loads(Path(args.json).read_text(encoding="utf-8"))
    per_culture, group_of = build_maps(cal)
    dsn = args.dsn or None
    if not dsn:
        import os

        dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нет DSN (используйте --dsn или DATABASE_URL)", file=sys.stderr)
        return 2
    engine = create_engine(dsn)

    if args.restore:
        rows = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        n = 0
        with engine.begin() as db:
            for row in rows:
                table = "dat_treatment_plan" if "planned_date" in row else "dat_field_operation"
                if table == "dat_field_operation":
                    db.execute(
                        text(
                            "update dat_field_operation set date = :date, "
                            "operation_type = :operation_type where id = :id"
                        ),
                        {k: row[k] for k in ("date", "operation_type", "id")},
                    )
                else:
                    db.execute(
                        text(
                            "update dat_treatment_plan set planned_date = :planned_date, "
                            "operation_type = :operation_type, status = :status where id = :id"
                        ),
                        {k: row[k] for k in ("planned_date", "operation_type", "status", "id")},
                    )
                n += 1
        print(f"restore: восстановлено строк {n} из {args.restore}")
        return 0

    with engine.connect() as db:
        cycles = (
            db.execute(
                text(
                    "select cc.id, cc.field_id, cc.year, c.name as culture "
                    "from dat_crop_cycle cc join ref_culture c on c.id = cc.culture_id "
                    "where cc.kind = 'main' order by cc.field_id, cc.year"
                )
            )
            .mappings()
            .all()
        )
        fields = {
            r["id"]: r["name"]
            for r in db.execute(text("select id, name from dat_field")).mappings().all()
        }
        ops = (
            db.execute(
                text(
                    "select id, field_id, date, operation_type, status "
                    "from dat_field_operation where status <> 'cancelled' order by field_id, date"
                )
            )
            .mappings()
            .all()
        )
        plans = (
            db.execute(
                text(
                    "select tp.id, tp.cycle_id, tp.planned_date, tp.operation_type, "
                    "tp.status, cc.year from dat_treatment_plan tp "
                    "join dat_crop_cycle cc on cc.id = tp.cycle_id "
                    "where tp.status not in ('cancelled') order by cc.field_id, tp.planned_date"
                )
            )
            .mappings()
            .all()
        )

    # индексы циклов: поле → год → (cycle_id, культура); поле → список лет
    by_field: dict[uuid.UUID, dict[int, tuple]] = {}
    for r in cycles:
        by_field.setdefault(r["field_id"], {})[r["year"]] = (r["id"], r["culture"])

    def cycle_years(field_id, day: date):
        """Кандидаты циклов операции по правилу §2 (год = год уборки),
        в порядке предпочтения: месяц ≥ 9 → (Y+1, Y); ≤ 7 → (Y, Y−1);
        август → (Y+1, Y, Y−1)."""
        years = by_field.get(field_id, {})
        if day.month >= 9:
            order = (day.year + 1, day.year)
        elif day.month <= 7:
            order = (day.year, day.year - 1)
        else:
            order = (day.year + 1, day.year, day.year - 1)
        return [y for y in order if y in years]

    unknown = Counter()
    violations: list[dict] = []

    def classify(kind, row, culture, day, op_type, ref):
        """Проверить op_type/day против окон культуры; вернуть предписание."""
        windows = per_culture.get(culture)
        if windows is None:
            unknown[culture] += 1
            return None
        group = group_of[culture]
        # seed_treatment планов проверяем по окну seeding
        lookup = "seeding" if op_type == "seed_treatment" else op_type
        win = windows.get(lookup)
        if win is None and op_type == "other":
            return None  # «прочее» не нормируется
        if group == "fallow" and win is None:
            # пар: вегетационные запрещены, fertilizing — только осеннее
            if op_type == "fertilizing":
                if not in_window(day, (8, 20), (10, 30)):
                    target = clamp_to_window(day, (8, 20), (10, 30), day.year)
                    return {"action": "shift", "date": target}
                return None
            return {
                "action": "retype_tillage",
                "date": clamp_to_window(day, (4, 1), (10, 30), day.year),
            }
        if win is None:
            return None  # операции нет в карте группы — не нормируем
        # ожидаемый календарный год операции цикла ref: озимый сев — осень
        # ref−1; озимые tillage — и после предшественника (ref−1), и после
        # уборки (ref); прочие операции — год цикла (все окна в одном году)
        if group in ("winter_cereals", "winter_oilseeds"):
            if op_type == "seeding":
                expected = {ref - 1}
            elif op_type == "tillage":
                expected = {ref - 1, ref}
            else:
                expected = {ref}
        else:
            expected = {ref}
        if in_window(day, *win) and day.year in expected:
            return None
        year = min(expected)
        target = clamp_to_window(day, *win, year)
        if target == day:
            return None
        action = "shift"
        if kind == "plan" and group == "fallow":
            # план по пару с вегетационной операцией: tillage запрещён CHECK
            # таблицы — помечаем cancelled (нелегален целиком)
            return {"action": "cancel"}
        return {"action": action, "date": target}

    # ── журнал ──
    for r in ops:
        cands = cycle_years(r["field_id"], r["date"])
        if not cands:
            continue  # цикла сезона нет — вне масштаба #158 (посев вне циклов)
        # среди кандидатов ищем цикл, чьё окно операции легально покрывает
        # дату (поздние яровые: уборка кукурузы/свёклы в октябре относится
        # к циклу того же года, а не к озимым следующего)
        verdict = None
        for y in cands:
            cult = by_field[r["field_id"]][y][1]
            v = classify("op", r, cult, r["date"], r["operation_type"], y)
            if v is None:  # легально в цикле y
                verdict = None
                break
            if verdict is None:
                verdict = v
                year, culture = y, cult
        if verdict is None:
            continue
        violations.append(
            {
                "table": "dat_field_operation",
                "id": str(r["id"]),
                "field": fields.get(r["field_id"], "?"),
                "cycle_year": year,
                "culture": culture,
                "op": r["operation_type"],
                "date": str(r["date"]),
                "verdict": verdict,
            }
        )

    # ── планы ──
    for r in plans:
        culture = dict((c["id"], c["culture"]) for c in cycles).get(r["cycle_id"])
        if culture is None or r["planned_date"] is None:
            continue
        verdict = classify("plan", r, culture, r["planned_date"], r["operation_type"], r["year"])
        if verdict:
            violations.append(
                {
                    "table": "dat_treatment_plan",
                    "id": str(r["id"]),
                    "field": "?",
                    "cycle_year": r["year"],
                    "culture": culture,
                    "op": r["operation_type"],
                    "date": str(r["planned_date"]),
                    "verdict": verdict,
                }
            )

    # ── отчёт ──
    print(f"операций журнала: {len(ops)}, планов: {len(plans)}")
    print(f"нарушений техокон: {len(violations)}")
    by_group = Counter((v["culture"], v["op"], v["verdict"]["action"]) for v in violations)
    for (culture, op, action), n in sorted(by_group.items()):
        print(f"  {culture:28} {op:12} {action:15} ×{n}")
    if unknown:
        print("культуры без карты:", dict(unknown))
    ctrl = {f.split("/")[0] for f in args.control.split(",")}
    print("\n-- контрольные поля --")
    for v in violations:
        if str(v["field"]).split("/")[0] in ctrl:
            print(
                f"  {v['field']} ({v['culture']}, цикл {v['cycle_year']}): "
                f"{v['op']} {v['date']} → {v['verdict']['action']} {v['verdict'].get('date', '')}"
            )

    if args.fix:
        backup = []
        with engine.begin() as db:
                if v["table"] == "dat_field_operation":
                    row = (
                        db.execute(
                            text("select * from dat_field_operation where id = :i"), {"i": v["id"]}
                        )
                        .mappings()
                        .first()
                    )
                    backup.append(dict(row))
                    if v["verdict"]["action"] == "shift":
                        db.execute(
                            text("update dat_field_operation set date = :d where id = :i"),
                            {"d": v["verdict"]["date"], "i": v["id"]},
                        )
                    else:  # retype_tillage
                        db.execute(
                            text(
                                "update dat_field_operation set operation_type = 'tillage', "
                                "date = :d where id = :i"
                            ),
                            {"d": v["verdict"]["date"], "i": v["id"]},
                        )
                else:
                    row = (
                        db.execute(
                            text("select * from dat_treatment_plan where id = :i"), {"i": v["id"]}
                        )
                        .mappings()
                        .first()
                    )
                    backup.append(dict(row))
                    if v["verdict"]["action"] == "shift":
                        db.execute(
                            text("update dat_treatment_plan set planned_date = :d where id = :i"),
                            {"d": v["verdict"]["date"], "i": v["id"]},
                        )
                    else:  # cancel — план нелегален для цикла-пара
                        db.execute(
                            text(
                                "update dat_treatment_plan set status = 'cancelled' where id = :i"
                            ),
                            {"i": v["id"]},
                        )
        path = Path(args.backup or f"ops_backup_{date.today():%Y%m%d}.json")
        path.write_text(
            json.dumps(backup, default=str, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n--fix: правок применено {len(violations)}, бэкап: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
