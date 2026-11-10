"""Ключевые даты сезона культуры для ретро-рекомендаций (#159, docs/09 §9).

Даты «перед севом / подкормка / гербициды / укосы / уборка» по группе
культуры (задача #159): озимые, яровые зерновые, пропашные, многолетние
травы. Дефолты — агрономические ориентиры Тюменской области (стенд);
если в плане/журнале поля есть реальная работа того же типа в окне ±25 сут
вокруг дефолта — берём её дату минус 3 дня (рекомендация «перед работой»).

Интеграция с типовыми техкартами #158: когда JSON техкарт зафиксирован,
season_key_dates принимает tech_map ({культура: [{label, month, day,
operation_type}]}) и использует его вместо дефолтов; пока tech_map=None.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agronomy import CropCycle, FieldOperation
from app.models.references import Culture

logger = logging.getLogger(__name__)

#: маркеры групп культур (как детекторы §8.2 v1.1 / hazards #139)
WINTER_MARKERS = ("озим", "рожь", "тритикале")
ROW_CROP_MARKERS = ("кукуруз", "подсолнеч", "свёкл", "свекл", "картофел", "соя", "рапс яровой")
GRASS_MARKERS = ("трав", "люцерн", "клевер", "донник")

#: сдвиг «рекомендация перед работой»: дата плана минус N суток
PLAN_LEAD_DAYS = 3
#: окно поиска реальной работы вокруг дефолтной даты, ±сут
PLAN_MATCH_WINDOW_DAYS = 25

#: дефолты по группам: (label, месяц, день, тип работы для сверки с планом)
_DEFAULTS: dict[str, list[tuple[str, int, int, str]]] = {
    "winter": [
        ("перед посевом озимых", 9, 15, "seeding"),
        ("весенняя подкормка", 4, 1, "fertilizing"),
        ("перед уборкой", 7, 1, "harvesting"),
    ],
    "spring": [
        ("предпосевная подготовка", 4, 25, "fertilizing"),
        ("гербицидное окно", 6, 5, "spraying"),
        ("перед уборкой", 8, 1, "harvesting"),
    ],
    "row_crop": [
        ("перед посевом", 5, 10, "seeding"),
        ("междурядная обработка", 6, 15, "tillage"),
        ("десикация/перед уборкой", 9, 5, "harvesting"),
    ],
    "grass": [
        ("старт отрастания", 4, 15, "fertilizing"),
        # укосы добавляются по NDVI-провалам (см. _grass_cut_dates)
    ],
}


#: типовые техокна из #158 (docs/reports/tech-calendar-2026.json, версия 1.0,
#: чернозёмная зона): группа → культуры → операция → (from, to), месяц-день.
#: Ключевые даты ретро = начало окна операции − 3 сут («перед работой»);
#: озимые/рапс озимый сеют в году Y−1 (окно с августа, год цикла = уборка).
#: Источник canonical — JSON; константа синхронизирована вручную (#159).
TECH_CALENDAR: dict[str, dict] = {
    "winter_cereals": {
        "cultures": ["Озимая пшеница", "Рожь", "Тритикале", "Ячмень озимый"],
        "windows": {
            "seeding": "08-15",
            "fertilizing": "03-10",
            "spraying": "03-20",
            "harvesting": "07-01",
        },
    },
    "spring_cereals": {
        "cultures": ["Яровая пшеница", "Ячмень", "Овёс"],
        "windows": {
            "seeding": "04-05",
            "fertilizing": "03-25",
            "spraying": "05-01",
            "harvesting": "07-10",
        },
    },
    "buckwheat_millet": {
        "cultures": ["Гречиха", "Просо"],
        "windows": {
            "seeding": "05-05",
            "fertilizing": "04-20",
            "spraying": "06-01",
            "harvesting": "08-15",
        },
    },
    "corn": {
        "cultures": ["Кукуруза", "Кукуруза на силос"],
        "windows": {
            "seeding": "04-25",
            "fertilizing": "04-15",
            "spraying": "05-10",
            "harvesting": "08-20",
        },
    },
    "legumes": {
        "cultures": ["Горох", "Чечевица"],
        "windows": {
            "seeding": "04-05",
            "fertilizing": "03-25",
            "spraying": "05-05",
            "harvesting": "07-10",
        },
    },
    "soy": {
        "cultures": ["Соя"],
        "windows": {
            "seeding": "04-25",
            "fertilizing": "04-20",
            "spraying": "05-25",
            "harvesting": "09-05",
        },
    },
    "winter_oilseeds": {
        "cultures": ["Рапс озимый"],
        "windows": {
            "seeding": "08-05",
            "fertilizing": "03-05",
            "spraying": "03-20",
            "harvesting": "07-05",
        },
    },
    "spring_oilseeds": {
        "cultures": ["Рапс", "Подсолнечник"],
        "windows": {
            "seeding": "04-15",
            "fertilizing": "04-10",
            "spraying": "05-10",
            "harvesting": "08-01",
        },
    },
    "perennial_grasses": {
        "cultures": ["Люцерна", "Клевер с тимофеевкой", "Многолетние травы"],
        "windows": {
            "seeding": "04-10",
            "fertilizing": "03-15",
            "spraying": "04-10",
            "harvesting": "05-25",
        },
    },
    "annual_grasses": {
        "cultures": ["Однолетние травы"],
        "windows": {
            "seeding": "04-20",
            "fertilizing": "04-15",
            "spraying": "05-20",
            "harvesting": "06-25",
        },
    },
    "sugar_beet": {
        "cultures": ["Свёкла сахарная"],
        "windows": {
            "seeding": "04-05",
            "fertilizing": "03-25",
            "spraying": "05-05",
            "harvesting": "09-15",
        },
    },
    "potato": {
        "cultures": ["Картофель"],
        "windows": {
            "seeding": "04-20",
            "fertilizing": "04-10",
            "spraying": "05-25",
            "harvesting": "08-20",
        },
    },
    "vegetables": {
        "cultures": ["Овощи открытого грунта"],
        "windows": {
            "seeding": "05-05",
            "fertilizing": "04-20",
            "spraying": "06-01",
            "harvesting": "07-10",
        },
    },
    "fallow": {
        "cultures": ["Пар", "Пар занятый"],
        "windows": {"tillage": "04-01"},  # вегетационных операций нет — ретро-дат нет
    },
}

#: подписи ключевых дат по типу операции (тексты для KeyDate.label)
_OP_LABELS = {
    "seeding": "перед посевом",
    "fertilizing": "перед подкормкой",
    "spraying": "перед обработкой СЗР",
    "harvesting": "перед уборкой",
    "tillage": "перед обработкой почвы",
}

_OP_ORDER = ("seeding", "fertilizing", "spraying", "harvesting", "tillage")


def _calendar_entries_tech_map(
    tech_map: dict, culture_name: str | None
) -> list[tuple[str, int, int, str]] | None:
    """tech_map формата #158: {группа: {cultures, windows}} → entries."""
    if not culture_name:
        return None
    lowered = culture_name.strip().lower()
    for group in tech_map.values():
        cultures = group.get("cultures") if isinstance(group, dict) else None
        if not cultures or lowered not in {str(c).lower() for c in cultures}:
            continue
        entries = []
        for op in _OP_ORDER:
            window = (group.get("windows") or {}).get(op)
            if not window or not window.get("from"):
                continue
            month, day_ = (int(x) for x in str(window["from"]).split("-"))
            entries.append((_OP_LABELS[op], month, day_, op))
        return entries
    return None


def _calendar_entries(culture_name: str | None) -> list[tuple[str, int, int, str]] | None:
    """(label, месяц, день, операция) из техкарт #158 по имени культуры.

    None — культуры нет в календаре (fallback на маркеры групп/дефолты).
    """
    if not culture_name:
        return None
    lowered = culture_name.strip().lower()
    for group in TECH_CALENDAR.values():
        if lowered in {c.lower() for c in group["cultures"]}:
            entries = []
            for op in _OP_ORDER:
                window = group["windows"].get(op)
                if not window:
                    continue
                month, day_ = (int(x) for x in window.split("-"))
                entries.append((_OP_LABELS[op], month, day_, op))
            return entries
    return None


def _is_winter_calendar_group(culture_name: str | None) -> bool:
    """Группа календаря сеется в году Y−1 (озимые зерновые/рапс озимый)."""
    if not culture_name:
        return False
    lowered = culture_name.strip().lower()
    for key in ("winter_cereals", "winter_oilseeds"):
        if lowered in {c.lower() for c in TECH_CALENDAR[key]["cultures"]}:
            return True
    return False


@dataclass(frozen=True, slots=True)
class KeyDate:
    """Ключевая дата сезона: смысл и дата T ретро-рекомендации."""

    label: str
    date: date


def culture_group(culture_name: str | None) -> str:
    """Группа культуры: winter | spring | row_crop | grass (default spring)."""
    if not culture_name:
        return "spring"
    lowered = culture_name.lower()
    if any(m in lowered for m in WINTER_MARKERS):
        return "winter"
    if any(m in lowered for m in GRASS_MARKERS):
        return "grass"
    if any(m in lowered for m in ROW_CROP_MARKERS):
        return "row_crop"
    return "spring"


def _plan_shifted_date(
    db: Session, field_id: uuid.UUID, default: date, operation_type: str
) -> date:
    """Дата реальной работы того же типа около дефолта − 3 сут (есть план — берём его)."""
    window_from = default - timedelta(days=PLAN_MATCH_WINDOW_DAYS)
    window_to = default + timedelta(days=PLAN_MATCH_WINDOW_DAYS)
    row = db.execute(
        select(FieldOperation.date)
        .where(
            FieldOperation.field_id == field_id,
            FieldOperation.operation_type == operation_type,
            FieldOperation.date >= window_from,
            FieldOperation.date <= window_to,
        )
        .order_by(FieldOperation.date)
        .limit(1)
    ).first()
    if row is not None:
        shifted = row[0] - timedelta(days=PLAN_LEAD_DAYS)
        return shifted
    return default


def _grass_cut_dates(
    scenes: list[tuple[date, float]], season_year: int, max_cuts: int = 2
) -> list[date]:
    """Даты укосов трав по провалам NDVI: падение ≥ 0.15 после локального
    максимума (после укоса отрастание начинается заново) — до пика сезона
    обрабатывать поздно; берём дату провала + 3 сут (отрастание после укоса).
    """
    cuts: list[date] = []
    running_max: float | None = None
    for day, avg in scenes:
        if day.year != season_year:
            continue
        if running_max is None:
            running_max = avg
            continue
        if running_max - avg >= 0.15:
            cuts.append(day + timedelta(days=PLAN_LEAD_DAYS))
            running_max = avg  # после укоса новая база отрастания
        elif avg > running_max:
            running_max = avg
        if len(cuts) >= max_cuts:
            break
    return cuts


def season_key_dates(
    db: Session,
    field_id: uuid.UUID,
    culture_name: str | None,
    season_year: int,
    *,
    scenes: list[tuple[date, float]] | None = None,
    tech_map: dict | None = None,
) -> list[KeyDate]:
    """Ключевые даты T сезона season_year культуры поля (#159).

    Источник приоритетов: техкарты #158 (TECH_CALENDAR / tech_map) —
    начало агроокна − 3 сут; культуры нет в календаре — маркеры групп и
    дефолты задачи #159. Озимые: посев в году Y−1 (сезон = год уборки).
    scenes — (дата, avg NDVI) сезона, нужны только для укосов трав.
    """
    group = culture_group(culture_name)
    entries: list[tuple[str, int, int, str]] | None = None
    winter_calendar = False
    from_calendar = False
    if tech_map is not None:
        entries = _calendar_entries_tech_map(tech_map, culture_name)
        from_calendar = entries is not None
    if entries is None:
        entries = _calendar_entries(culture_name)
        from_calendar = entries is not None
        winter_calendar = _is_winter_calendar_group(culture_name)
    if entries is None:
        entries = _DEFAULTS[group]
    out: list[KeyDate] = []
    for label, month, day_, op_type in entries:
        sown_prev_year = (winter_calendar or group == "winter") and month >= 8
        year = season_year - 1 if sown_prev_year else season_year
        default = date(year, month, day_)
        if from_calendar:
            # техкарта даёт НАЧАЛО окна операции: рекомендация — за 3 сут до
            default -= timedelta(days=PLAN_LEAD_DAYS)
        out.append(
            KeyDate(
                label=label,
                date=_plan_shifted_date(db, field_id, default, op_type),
            )
        )
    if group == "grass" and scenes:
        for cut_day in _grass_cut_dates(scenes, season_year):
            out.append(KeyDate(label="укос (по NDVI)", date=cut_day))
    out.sort(key=lambda kd: kd.date)
    return out


def season_cultures(
    db: Session, field_id: uuid.UUID, year_from: int, year_to: int
) -> dict[int, str]:
    """main-культуры по сезонам поля: {год сезона: имя культуры}.

    Год цикла = год уборки; для озимых это и есть сезон (посев осенью Y−1).
    """
    rows = db.execute(
        select(CropCycle.year, CropCycle.culture_id).where(
            CropCycle.field_id == field_id,
            CropCycle.kind == "main",
            CropCycle.year >= year_from,
            CropCycle.year <= year_to,
        )
    ).all()
    culture_names = dict(db.execute(select(Culture.id, Culture.name)).all())
    return {int(year): culture_names[cid] for year, cid in rows if cid in culture_names}


__all__ = [
    "KeyDate",
    "culture_group",
    "season_key_dates",
    "season_cultures",
]


def operations_key_dates(
    db: Session, field_id: uuid.UUID, season_year: int, *, max_dates: int = 8
) -> list[KeyDate]:
    """Ключевые даты = даты техопераций сезона (#182, приказ #716).

    Плановые и фактические даты единой операции (уник, без сдвига −3).
    Операций в сезоне больше max_dates — по одной на (тип, месяц), чтобы
    не генерить десятки близких рекомендаций.
    """
    rows = db.execute(
        select(FieldOperation.operation_type, FieldOperation.planned_date, FieldOperation.fact_date)
        .where(
            FieldOperation.field_id == field_id,
            FieldOperation.cycle_id.isnot(None),
            FieldOperation.status != "cancelled",
        )
        .order_by(FieldOperation.planned_date, FieldOperation.fact_date)
    ).all()
    dates: set[date] = set()
    for _op_type, planned, fact in rows:
        for day in (planned, fact):
            if day is not None and day.year == season_year:
                dates.add(day)
    unique = sorted(dates)
    if len(unique) <= max_dates:
        return [KeyDate(label="техоперация", date=d) for d in unique]
    # по одной на (тип не важен) месяц: первая дата месяца
    seen_months: set[int] = set()
    out: list[KeyDate] = []
    for day in unique:
        if day.month not in seen_months:
            seen_months.add(day.month)
            out.append(KeyDate(label="техоперация", date=day))
    return out
