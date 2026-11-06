"""Распределение севооборота по реальным полям (v1-10, задача #66).

CLI ``python -m app.cli seed-rotation``: для каждого активного поля без
плана и циклов создаёт план севооборота на [текущий−back, текущий+forward]
и основные циклы культур по годам с соблюдением RULE-AGRO-01/02
(матрица предшественников и минимальный возврат культуры).

Допущение (задача #66): реальных исторических данных пользователь не
передал — культуры подбираются правдоподобно из справочника ref_culture,
детерминированно от natural key поля (external_id, иначе id): повторный
запуск на тех же данных даёт ту же ротацию, при этом идемпотентность
гарантируется пропуском полей, у которых уже есть план или циклы.
"""

import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agronomy import CropCycle, Field, RotationPlan
from app.models.references import Culture, PredecessorRule

logger = logging.getLogger(__name__)

# ротационный пул хозяйства: основные зерновые/масличные/бобовые (v1-10;
# имена из сида справочника app/seed.py, kind='main')
POOL_CULTURE_NAMES: tuple[str, ...] = (
    "Озимая пшеница",
    "Яровая пшеница",
    "Ячмень",
    "Овёс",
    "Кукуруза",
    "Подсолнечник",
    "Рапс",
    "Соя",
    "Горох",
    "Гречиха",
)
# пар вводим не каждому полю: каждому 4-му по ключу (не чаще раза в ротацию)
FALLOW_EVERY_N_FIELDS = 4
FALLOW_NAME = "Пар"


@dataclass
class SeedRotationReport:
    """Итог распределения: что создано, что пропущено."""

    year_from: int = 0
    year_to: int = 0
    fields_processed: int = 0
    plans_created: int = 0
    cycles_created: int = 0
    skipped_existing: int = 0
    missing_cultures: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "year_from": self.year_from,
            "year_to": self.year_to,
            "fields_processed": self.fields_processed,
            "plans_created": self.plans_created,
            "cycles_created": self.cycles_created,
            "skipped_existing": self.skipped_existing,
            "missing_cultures": self.missing_cultures,
            "violations": self.violations,
        }


def _current_year() -> int:
    return datetime.now(UTC).year


def _stable_key(field: Field) -> str:
    """Детерминированный ключ ротации поля: external_id (natural key
    импорта), иначе id — постоянный для одной и той же записи."""
    return field.external_id or str(field.id)


def _pick_sequence(
    rng: random.Random,
    *,
    pool: list[Culture],
    matrix: dict[tuple[uuid.UUID, uuid.UUID], str],
    fallow: Culture | None,
    years: list[int],
) -> list[Culture]:
    """Выбрать культуру на каждый год: без бан-пар RULE-AGRO-01 (матрица),
    без повтора одной культуры два года подряд, возврат не раньше
    repeat_years (RULE-AGRO-02). Детерминированно от rng."""
    sequence: list[Culture] = []
    last_used: dict[uuid.UUID, int] = {}
    fallow_used = False
    for i, year in enumerate(years):
        candidates = pool.copy()
        rng.shuffle(candidates)
        # пар — не чаще раза в ротацию и только не первым годом
        if fallow is not None and not fallow_used and i > 0 and i % 5 == 1:
            candidates.insert(0, fallow)
        chosen: Culture | None = None
        prev = sequence[-1] if sequence else None
        for culture in candidates:
            if prev is not None:
                if culture.id == prev.id:  # монокультура недопустима всегда
                    continue
                if matrix.get((prev.culture_id := prev.id, culture.id)) == "ban":
                    continue
            last = last_used.get(culture.id)
            if last is not None and year - last < culture.repeat_years:
                continue
            chosen = culture
            break
        if chosen is None:
            # все кандидаты заблокированы (не встречается при пуле ≥ 3 и
            # repeat_years ≤ 5): берём первого, отличного от прошлого года
            chosen = next(c for c in pool if prev is None or c.id != prev.id)
        if chosen.id == fallow.id if fallow is not None else False:
            fallow_used = True
        sequence.append(chosen)
        last_used[chosen.id] = year
    return sequence


def seed_rotation(
    db: Session, *, years_back: int = 4, years_forward: int = 4
) -> SeedRotationReport:
    """Распределить севооборот по активным полям без плана и циклов."""
    report = SeedRotationReport()
    current_year = _current_year()
    report.year_from = current_year - years_back
    report.year_to = current_year + years_forward
    years = list(range(report.year_from, report.year_to + 1))
    now = datetime.now(UTC)

    # пул культур справочника; отсутствие культуры — не ошибка, просто сузим пул
    cultures = {
        c.name: c
        for c in db.scalars(select(Culture).where(Culture.name.in_(POOL_CULTURE_NAMES)))
    }
    report.missing_cultures = sorted(set(POOL_CULTURE_NAMES) - cultures.keys())
    pool = [cultures[name] for name in POOL_CULTURE_NAMES if name in cultures]
    if not pool:
        raise ValueError("В ref_culture нет культур ротационного пула — сначала seed-references")
    fallow = db.scalar(select(Culture).where(Culture.name == FALLOW_NAME))

    # матрица совместимости (culture_id, predecessor_id) → verdict
    matrix = {
        (r.culture_id, r.predecessor_id): r.verdict
        for r in db.scalars(select(PredecessorRule))
    }

    # активные поля без циклов и активных планов (идемпотентность)
    fields = db.scalars(
        select(Field)
        .where(
            Field.status == "active",
            Field.id.not_in(select(CropCycle.field_id)),
            Field.id.not_in(
                select(RotationPlan.field_id).where(RotationPlan.status != "archived")
            ),
        )
        .order_by(Field.name)
    ).all()

    field_no = 0
    for field in fields:
        rng = random.Random(f"rotation:{_stable_key(field)}")
        with_fallow = fallow if field_no % FALLOW_EVERY_N_FIELDS == 0 and fallow else None
        sequence = _pick_sequence(
            rng, pool=pool, matrix=matrix, fallow=with_fallow, years=years
        )
        db.add(
            RotationPlan(
                field_id=field.id,
                year_from=report.year_from,
                year_to=report.year_to,
                status="approved",
                approved_by="CLI seed-rotation (v1-10, автоматическое распределение)",
                approved_at=now,
            )
        )
        report.plans_created += 1
        prev_cycle_id: uuid.UUID | None = None
        for year, culture in zip(years, sequence, strict=True):
            cycle = CropCycle(
                field_id=field.id,
                year=year,
                culture_id=culture.id,
                kind="main",
                predecessor_cycle_id=prev_cycle_id,
                status=(
                    "completed" if year < current_year
                    else "in_progress" if year == current_year
                    else "planned"
                ),
            )
            db.add(cycle)
            db.flush()  # id для связи следующего года (RULE-AGRO-04)
            prev_cycle_id = cycle.id
            report.cycles_created += 1
        report.fields_processed += 1
        field_no += 1

    db.commit()
    logger.info(
        "seed-rotation: полей %d, планов %d, циклов %d (годы %d–%d)",
        report.fields_processed,
        report.plans_created,
        report.cycles_created,
        report.year_from,
        report.year_to,
    )
    return report
