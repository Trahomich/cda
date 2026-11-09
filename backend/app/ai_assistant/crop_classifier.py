"""Классификатор культуры по NDVI-кривой сезона (docs/06 §7.9, #155).

Идея (фидбек #585, дизайн #586): вид культуры по спутнику не определяется —
не фантазируем; класс внутри группы надёжен:

- winter_grain (озимая зерновая): осенний подъём NDVI (октябрь–ноябрь
  года сева Y−1) и/или ранний весенний старт (апрель) при снежной зиме;
- spring_grain (яровая зерновая): без осенней вегетации, один главный
  пик в мае–июле;
- perennial_grass (многолетние травы): 2–4 «укосных» цикла — локальные
  максимумы с провалами ≥ 0.15 между укосами;
- fallow (пар): NDVI у пола весь сезон + высокий BSI (голая почва);
- uncertain: сцена мало или правила противоречат друг другу — честно
  не знаем.

Уверенность = 0.5·базы правила + 0.5·согласия сезонов истории (доля
сезонов 2023–2026 с тем же классом; мультисезонность усиливает вывод).
Чистые функции над собранными из БД сценами (ADR-02: в БД — выжимки).
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agronomy import CropCycle
from app.models.integrations import SatelliteScene
from app.models.references import Culture

logger = logging.getLogger(__name__)

#: классы классификатора (docs/06 §7.9)
WINTER_GRAIN = "winter_grain"
SPRING_GRAIN = "spring_grain"
PERENNIAL_GRASS = "perennial_grass"
FALLOW = "fallow"
UNCERTAIN = "uncertain"
CROP_CLASSES = (WINTER_GRAIN, SPRING_GRAIN, PERENNIAL_GRASS, FALLOW, UNCERTAIN)

# -- пороги (агрономические допущения; согласованы в #586) ------------------

#: озимая: NDVI развитого посева в октябре–ноябре года сева (Y−1)
WINTER_AUTUMN_NDVI = 0.35
WINTER_AUTUMN_MIN_SCENES = 2
#: озимая: ранний весенний старт — NDVI вегетации уже в апреле года уборки
WINTER_SPRING_NDVI = 0.30
#: яровая: осенней вегетации нет. Порог 0.30 (а не 0.25): стерня/сорняки
#: после уборки яровых держат NDVI ~0.2–0.3, тогда как развитой озимый
#: посев к октябрю–ноябрю уже ≥ 0.35 — порог между этими уровнями
SPRING_AUTUMN_NDVI_MAX = 0.30
#: яровая: развитой пик вегетации (ниже — это скорее пар/сорняки)
SPRING_PEAK_NDVI_MIN = 0.40
#: травы: провал между укосами (после укоса NDVI падает, потом отрастание)
GRASS_DIP = 0.15
GRASS_MIN_PEAKS = 2
#: травы: оба пика укосного цикла — развитой покров (трава к укосу высокая).
#: Без порога «пик → провал (УБОРКА яровой) → отрастание сорняков на жнивье»
#: ошибочно выглядит укосом: сорняковый пик ниже 0.45
GRASS_PEAK_NDVI_MIN = 0.45
#: пар: NDVI весь сезон у пола (сорняки единично)
FALLOW_NDVI_MAX = 0.30
#: пар: доля голой почвы по BSI (пик голой почвы в обработках)
FALLOW_BSI_MIN = 0.10
#: минимум сцен для вывода (меньше — uncertain, не гадаем)
MIN_SCENES = 6
#: базовая уверенность правила (все условия) / частичного правила
CONF_BASE_STRONG = 0.8
CONF_BASE_WEAK = 0.6


@dataclass(frozen=True, slots=True)
class CropClassification:
    """Результат классификации сезона: класс, уверенность, доказательства."""

    crop_class: str
    confidence: float
    evidences: list[str] = field(default_factory=list)


def _scenes_by_index(
    db: Session, field_id: uuid.UUID, index: str, date_from: date, date_to: date
) -> list[tuple[date, float]]:
    return [
        (day, float(avg))
        for day, avg in db.execute(
            select(SatelliteScene.captured_at, SatelliteScene.index_avg)
            .where(
                SatelliteScene.field_id == field_id,
                SatelliteScene.index == index,
                SatelliteScene.captured_at >= date_from,
                SatelliteScene.captured_at <= date_to,
            )
            .order_by(SatelliteScene.captured_at)
        ).all()
    ]


def _local_maxima(series: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Локальные максимумы серии (по датам по возрастанию).

    Крайние точки учитываются, когда они выше единственного соседа (первый
    укос часто виден уже первой сценой сезона).
    """
    out: list[tuple[date, float]] = []
    for i, (day, value) in enumerate(series):
        left = series[i - 1][1] if i > 0 else float("-inf")
        right = series[i + 1][1] if i < len(series) - 1 else float("-inf")
        if value >= left and value > right:
            out.append((day, value))
    return out


def classify_curve(
    ndvi: list[tuple[date, float]],
    ndsi: list[tuple[date, float]] | None = None,
    bsi: list[tuple[date, float]] | None = None,
    *,
    season_year: int,
) -> CropClassification:
    """Класс одной кривой: ndvi — сцены окна сезона (сентябрь Y−1…июль Y).

    Правила в порядке убывания специфичности: озимая → травы → яровая →
    пар; противоречие или < MIN_SCENES сцен — uncertain (не гадаем).
    """
    if len(ndvi) < MIN_SCENES:
        return CropClassification(
            UNCERTAIN, 0.3, [f"сцен мало ({len(ndvi)} < {MIN_SCENES}) — не гадаем"]
        )

    autumn = [(d, v) for d, v in ndvi if d.year == season_year - 1 and d.month in (10, 11, 12)]
    autumn_hits = [v for _, v in autumn if v >= WINTER_AUTUMN_NDVI]
    season = [(d, v) for d, v in ndvi if d.year == season_year]
    april = [v for d, v in season if d.month == 4]
    may_july = [(d, v) for d, v in season if 5 <= d.month <= 7]
    apr_sep = [(d, v) for d, v in season if 4 <= d.month <= 9]
    season_max = max((v for _, v in season), default=0.0)
    bsi_max = max((v for _, v in (bsi or [])), default=None)

    evidences: list[str] = []

    # --- озимая: осенний подъём ИЛИ ранний апрельский старт -----------
    winter_evidences: list[str] = []
    if len(autumn_hits) >= WINTER_AUTUMN_MIN_SCENES:
        winter_evidences.append(
            f"осенний подъём NDVI: {len(autumn_hits)} сцен октября–декабря "
            f"≥ {WINTER_AUTUMN_NDVI:.2f}"
        )
    if april and max(april) >= WINTER_SPRING_NDVI:
        winter_evidences.append(
            f"ранний весенний старт: NDVI апреля {max(april):.2f} ≥ "
            f"{WINTER_SPRING_NDVI:.2f}" + (" при снежной зиме (NDSI есть)" if ndsi else "")
        )
    if winter_evidences:
        return CropClassification(
            WINTER_GRAIN, CONF_BASE_STRONG, ["озимая зерновая: " + "; ".join(winter_evidences)]
        )

    # --- пар: NDVI у пола + BSI голой почвы (до яровой: яровая требует
    # развитого пика, иначе низкий NDVI примет за яровую) --------------
    if season_max < FALLOW_NDVI_MAX:
        if bsi_max is None or bsi_max >= FALLOW_BSI_MIN:
            return CropClassification(
                FALLOW,
                CONF_BASE_STRONG if bsi_max is not None else CONF_BASE_WEAK,
                [
                    f"пар: max NDVI сезона {season_max:.2f} < {FALLOW_NDVI_MAX:.2f}"
                    + (
                        f", BSI до {bsi_max:.2f} ≥ {FALLOW_BSI_MIN:.2f} — голая почва"
                        if bsi_max is not None
                        else " (BSI нет — по одному NDVI)"
                    ),
                ],
            )

    # --- травы: укосные циклы (ВЫСОКИЕ пики с провалами; низкий второй
    # «пик» — это жнивье после уборки яровой, не отрастание травы) ------
    if season_max >= FALLOW_NDVI_MAX:
        peaks = [p for p in _local_maxima(apr_sep) if p[1] >= GRASS_PEAK_NDVI_MIN]
        cycles: list[tuple[date, float, float]] = []  # пик, провал, следующий пик
        for i in range(len(peaks) - 1):
            between = [v for d, v in apr_sep if peaks[i][0] < d < peaks[i + 1][0]]
            if between:
                dip = peaks[i][1] - min(between)
                if dip >= GRASS_DIP:
                    cycles.append((peaks[i][0], dip, peaks[i + 1][0]))
        if len(cycles) + 1 >= GRASS_MIN_PEAKS and cycles:
            return CropClassification(
                PERENNIAL_GRASS,
                CONF_BASE_STRONG,
                [
                    "многолетние травы: укосные циклы — "
                    + "; ".join(
                        f"пик {p.isoformat()} → провал −{dip:.2f} → пик {n.isoformat()}"
                        for p, dip, n in cycles[:3]
                    )
                ],
            )

    # --- яровая: без осенней вегетации, один главный пик май–июль -----
    autumn_max = max((v for _, v in autumn), default=None)
    april_no_winter = bool(april) and max(april) < WINTER_SPRING_NDVI
    if (
        may_july
        and season_max >= SPRING_PEAK_NDVI_MIN
        and (
            (autumn_max is not None and autumn_max < SPRING_AUTUMN_NDVI_MAX)
            # осенних сцен нет (ранняя история) — озимых отсекаем апрельским NDVI
            or (autumn_max is None and april_no_winter)
        )
    ):
        top = max(may_july, key=lambda dv: dv[1])
        if autumn_max is not None:
            evidences.append(
                f"осенней вегетации нет (max NDVI осени {autumn_max:.2f} < "
                f"{SPRING_AUTUMN_NDVI_MAX:.2f})"
            )
        else:
            evidences.append(
                "осенних сцен в истории нет; апрель без озимых всходов "
                f"(NDVI {max(april):.2f} < {WINTER_SPRING_NDVI:.2f})"
            )
        evidences.append(f"главный пик {top[0].isoformat()}: NDVI {top[1]:.2f} в мае–июле")
        # противоречие — укосный ЦИКЛ: соперничающий пик с настоящим провалом
        # между ним и главным (шумное плато без провала — нормальная яровая)
        mj_peaks = _local_maxima(may_july)
        has_mow_cycle = False
        for d, v in mj_peaks:
            if d == top[0] or v < GRASS_PEAK_NDVI_MIN:
                continue
            lo, hi = min(d, top[0]), max(d, top[0])
            between = [b for bd, b in may_july if lo < bd < hi]
            dip = min(v, top[1]) - min(between)
            if dip >= GRASS_DIP:
                has_mow_cycle = True
                evidences.append(
                    f"сопоставимый пик {d.isoformat()} ({v:.2f}) с провалом "
                    f"−{dip:.2f} — укос травы?"
                )
                break
        if has_mow_cycle:
            return CropClassification(
                UNCERTAIN, CONF_BASE_WEAK, evidences
            )
        base = CONF_BASE_STRONG if autumn_max is not None else CONF_BASE_WEAK
        return CropClassification(SPRING_GRAIN, base, ["яровая: " + "; ".join(evidences)])

    return CropClassification(
        UNCERTAIN, CONF_BASE_WEAK, ["правила противоречат или кривая нетипична — не гадаем"]
    )


def classify_field_season(db: Session, field_id: uuid.UUID, year: int) -> CropClassification:
    """Класс сезона year поля по сценам из БД + уверенность по истории.

    Уверенность: 0.5·базы правила + 0.5·доли сезонов истории поля (все
    годы с данными, кроме текущего) с тем же классом — мультисезонность
    2023–2026 усиливает вывод (травы остаются травами, пар полем).
    """
    ndvi = _scenes_by_index(db, field_id, "ndvi", date(year - 1, 9, 1), date(year, 9, 30))
    if not ndvi:
        return CropClassification(UNCERTAIN, 0.0, ["нет NDVI-сцен сезона"])
    ndsi = _scenes_by_index(db, field_id, "ndsi", date(year - 1, 12, 1), date(year, 3, 31))
    bsi = _scenes_by_index(db, field_id, "bsi", date(year, 1, 1), date(year, 9, 30))
    result = classify_curve(ndvi, ndsi, bsi, season_year=year)

    # мультисезонность: классы прочих лет с данными
    years = [
        int(y)
        for y in db.scalars(
            select(func.extract("year", SatelliteScene.captured_at))
            .where(SatelliteScene.field_id == field_id, SatelliteScene.index == "ndvi")
            .distinct()
        )
        if int(y) != year
    ]
    same = 0
    checked = 0
    for other in years:
        other_ndvi = _scenes_by_index(
            db, field_id, "ndvi", date(other - 1, 9, 1), date(other, 9, 30)
        )
        other_cls = classify_curve(other_ndvi, season_year=other)
        if other_cls.crop_class == UNCERTAIN:
            continue
        checked += 1
        if other_cls.crop_class == result.crop_class:
            same += 1
    if result.crop_class != UNCERTAIN and checked:
        agreement = same / checked
        confidence = round(0.5 * result.confidence + 0.5 * agreement, 2)
        result = CropClassification(
            result.crop_class,
            confidence,
            result.evidences + [f"история: {same} из {checked} сезонов — тот же класс"],
        )
    return result


# -- сверка с севооборотом ---------------------------------------------------

#: маркеры озимых/трав в имени культуры (озимые — как у детекторов §8.2)
WINTER_CULTURE_MARKERS = ("озим", "рожь", "тритикале")
GRASS_CULTURE_MARKERS = ("трав", "люцерн", "клевер", "эспарцет", "донник")


def recorded_culture(db: Session, field_id: uuid.UUID, year: int) -> tuple[str | None, str | None]:
    """(culture_name, culture_kind) main-цикла года; (None, None) — цикла нет."""
    row = db.execute(
        select(Culture.name, Culture.kind)
        .join(CropCycle, CropCycle.culture_id == Culture.id)
        .where(
            CropCycle.field_id == field_id,
            CropCycle.year == year,
            CropCycle.kind == "main",
        )
        .limit(1)
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


def expected_class(culture_name: str | None, culture_kind: str | None) -> str | None:
    """Ожидаемый класс по записанной культуре; None — сверять не с чем.

    Консервативно: сверяем только однозначные записи — озимые (маркеры),
    пар (kind=fallow) и многолетние травы (маркеры). Вид внутри яровых
    (пшеница/гречиха/кукуруза…) по кривой не различаем — mismatch не ищем.
    """
    if culture_kind == "fallow":
        return FALLOW
    if culture_name is None:
        return None
    lowered = culture_name.lower()
    if any(marker in lowered for marker in WINTER_CULTURE_MARKERS):
        return WINTER_GRAIN
    if any(marker in lowered for marker in GRASS_CULTURE_MARKERS):
        return PERENNIAL_GRASS
    return None


__all__ = [
    "CROP_CLASSES",
    "CropClassification",
    "FALLOW",
    "PERENNIAL_GRASS",
    "SPRING_GRAIN",
    "UNCERTAIN",
    "WINTER_GRAIN",
    "classify_curve",
    "classify_field_season",
    "expected_class",
    "recorded_culture",
]
