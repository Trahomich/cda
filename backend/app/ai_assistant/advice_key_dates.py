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

    Озимые: сезон = год уборки Y → «перед посевом» 15.09.(Y−1).
    scenes — (дата, avg NDVI) сезона, нужны только для укосов трав.
    tech_map — техкарты #158 (будущая интеграция; None — дефолты).
    """
    group = culture_group(culture_name)
    out: list[KeyDate] = []
    if tech_map is not None:
        rows = tech_map.get(culture_name or "", [])
        entries = [
            (str(r["label"]), int(r["month"]), int(r["day"]), str(r["operation_type"]))
            for r in rows
        ]
    else:
        entries = _DEFAULTS[group]
    for label, month, day_, op_type in entries:
        year = season_year - 1 if group == "winter" and month >= 9 else season_year
        default = date(year, month, day_)
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
