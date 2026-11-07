"""Числовые детекторы аномалий поля (docs/09-ai.md §8.2, docs/06 §7.4, #93).

Чистые функции над собранными из БД данными: сцены NDVI (dat_satellite_scene,
чистые cloud ≤ 60 — в БД других нет, 05 §5), факты погоды
(dat_weather_observation), последняя агрохимпроба (ref_agrochem). Каждый
детектор возвращает InsightItem или None; пустые не попадают в ответ.

Пороги правила вымокания и VRA-неоднородности импортируются из точек истины
(waterlog.py #91, vra.py #94) — дублирование чисел не допускается.

Допущения (в БД нет прямых норм, зафиксированы в константах):
- waterlogging alert: контрактом задан «доля ≥ 30 % пикселей», зональная
  статистика хранит только ndwi_mean/ndwi_p90; прокси — ndwi_mean ≥ 0.10
  (среднее по полю у порога означает высокую долю влажных пикселей);
- drought «GDD выше нормы»: норма среднесуточного GDD вегетации — 10 °C·сут
  (база 5 °C), превышение — средний суточный GDD за окно 14 сут выше нормы;
- ndvi_drop ищется среди соседних чистых сцен последних 30 суток от
  последней сцены (выводы — о текущем состоянии поля).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_assistant.schemas import InsightItem
from app.models.integrations import SatelliteScene, WeatherObservation
from app.models.agronomy import CropCycle
from app.models.references import Agrochem, Culture
from app.modules.satellite.vra import VRA_MIN_IQR
from app.modules.satellite.waterlog import (
    WATERLOG_NDWI_P90,
    WATERLOG_NDVI_MEAN,
    WATERLOG_PRECIP_MM_7D,
    waterlogged,
)

#: ndvi_drop: падение NDVI между соседними чистыми сценами (09-ai §8.2)
NDVI_DROP_WARN = 0.15
NDVI_DROP_ALERT = 0.25
NDVI_DROP_WINDOW_DAYS = 30

#: ndvi_stagnation: прирост за 3 сцены, потолок «активной вегетации» и сезон
NDVI_STAGNATION_MAX_GAIN = 0.05
NDVI_STAGNATION_MAX_MEAN = 0.7
NDVI_STAGNATION_MIN_MEAN = 0.3  # ниже — вегетации нет, стагнация не о чём
#: окно вегетации (09 §8.2 v1.1): обычные культуры — апрель–октябрь,
#: озимые (осенний сев + весенне-раннелетнее развитие) — март–июнь и сентябрь–ноябрь
NDVI_STAGNATION_SEASON_MONTHS = frozenset(range(4, 11))
NDVI_STAGNATION_WINTER_MONTHS = frozenset({3, 4, 5, 6, 9, 10, 11})
#: маркеры озимых в имени культуры ref_culture.name (09 §8.2 v1.1)
WINTER_CULTURE_MARKERS = ("озим", "рожь", "тритикале")

#: drought: осадки за 14 сут и норма среднесуточного GDD (см. докстринг)
DROUGHT_WINDOW_DAYS = 14
DROUGHT_PRECIP_MM = 10.0
GDD_BASE_C = 5.0
GDD_NORM_DAILY_C = 10.0
GDD_MIN_DAYS = 7  # меньше — окно непредставительно, детектор молчит

#: ndvi_lag_last_year: отставание от прошлой даты ± окно поиска сцены
NDVI_LAG_THRESHOLD = 0.1
NDVI_LAG_WINDOW_DAYS = 7

#: agrochem_low: нижние границы pH и гумуса (09-ai §8.2)
AGROCHEM_PH_MIN = 5.5
AGROCHEM_HUMUS_MIN_PCT = 2.0


@dataclass(frozen=True, slots=True)
class SceneStat:
    """Статистика чистой NDVI-сцены, нужная детекторам."""

    scene_id: uuid.UUID
    captured_at: date
    ndvi_mean: float
    ndwi_mean: float | None
    ndwi_p90: float | None


def load_scenes(db: Session, field_id: uuid.UUID, limit: int = 200) -> list[SceneStat]:
    """Чистые NDVI-сцены поля по возрастанию даты (для трендов и лага)."""
    rows = db.execute(
        select(
            SatelliteScene.id,
            SatelliteScene.captured_at,
            SatelliteScene.index_avg,
            SatelliteScene.ndwi_mean,
            SatelliteScene.ndwi_p90,
        )
        .where(SatelliteScene.field_id == field_id, SatelliteScene.index == "ndvi")
        .order_by(SatelliteScene.captured_at.desc(), SatelliteScene.created_at.desc())
        .limit(limit)
    ).all()
    return [
        SceneStat(
            scene_id=scene_id,
            captured_at=captured_at,
            ndvi_mean=float(index_avg),
            ndwi_mean=float(ndwi_mean) if ndwi_mean is not None else None,
            ndwi_p90=float(ndwi_p90) if ndwi_p90 is not None else None,
        )
        for scene_id, captured_at, index_avg, ndwi_mean, ndwi_p90 in reversed(rows)
    ]


def latest_agrochem(db: Session, field_id: uuid.UUID) -> Agrochem | None:
    """Последняя агрохимическая проба поля."""
    return db.scalars(
        select(Agrochem)
        .where(Agrochem.field_id == field_id)
        .order_by(Agrochem.sample_date.desc(), Agrochem.created_at.desc())
        .limit(1)
    ).first()


def current_culture_name(db: Session, field_id: uuid.UUID, day: date) -> str | None:
    """Культура текущего main-цикла поля на дату day (09 §8.2 v1.1).

    У цикла в БД только год сезона. Допущение: год цикла — год уборки,
    поэтому озимым, посеянным осенью Y−1, соответствует cycle.year = Y:
    - месяцы 9–12: сначала цикл day.year + 1 (озимые под будущий сезон),
      затем day.year;
    - месяцы 1–8: сначала day.year, затем day.year − 1 (озимые с осени).
    Нет цикла — None (фолбэк «обычное» окно 4–10).
    """
    years = (day.year + 1, day.year) if day.month >= 9 else (day.year, day.year - 1)
    for year in years:
        name = db.scalar(
            select(Culture.name)
            .join(CropCycle, CropCycle.culture_id == Culture.id)
            .where(
                CropCycle.field_id == field_id,
                CropCycle.year == year,
                CropCycle.kind == "main",
            )
            .order_by(CropCycle.created_at.desc())
            .limit(1)
        )
        if name is not None:
            return name
    return None


def _is_winter_culture(culture_name: str | None) -> bool:
    """Озимая культура по маркерам имени («озим», «рожь», «тритикале»)."""
    if not culture_name:
        return False
    lowered = culture_name.lower()
    return any(marker in lowered for marker in WINTER_CULTURE_MARKERS)


def weather_window_14d(
    db: Session, field_id: uuid.UUID, day: date
) -> tuple[float | None, float | None, int]:
    """Осадки (мм) и сумма GDD (°C·сут) за 14 суток до day включительно.

    GDD = max(0, (t_min + t_max)/2 − 5). None — наблюдений в окне нет;
    NULL-осадки считаются нулём, сутки без обеих температур в GDD не идут.
    """
    rows = db.execute(
        select(
            WeatherObservation.precipitation_mm,
            WeatherObservation.temperature_min_c,
            WeatherObservation.temperature_max_c,
        ).where(
            WeatherObservation.field_id == field_id,
            WeatherObservation.observed_at >= day - timedelta(days=DROUGHT_WINDOW_DAYS - 1),
            WeatherObservation.observed_at <= day,
        )
    ).all()
    if not rows:
        return None, None, 0
    precip = sum(float(mm) if mm is not None else 0.0 for mm, _, _ in rows)
    gdd = sum(
        max(0.0, (float(t_min) + float(t_max)) / 2 - GDD_BASE_C)
        for _, t_min, t_max in rows
        if t_min is not None and t_max is not None
    )
    gdd_days = sum(1 for _, t_min, t_max in rows if t_min is not None and t_max is not None)
    return round(precip, 1), round(gdd, 1), gdd_days


def _scene_ref(scene: SceneStat) -> dict:
    return {"scene_id": str(scene.scene_id), "captured_at": scene.captured_at.isoformat()}


def detect_ndvi_drop(scenes: Sequence[SceneStat]) -> InsightItem | None:
    """warn: NDVI упал ≥ 0.15 между соседними датами (alert при ≥ 0.25)."""
    if len(scenes) < 2:
        return None
    window_from = scenes[-1].captured_at - timedelta(days=NDVI_DROP_WINDOW_DAYS)
    recent = [s for s in scenes if s.captured_at >= window_from]
    worst = None
    for prev, cur in zip(recent, recent[1:], strict=False):
        drop = round(prev.ndvi_mean - cur.ndvi_mean, 3)
        if drop >= NDVI_DROP_WARN and (worst is None or drop > worst[0]):
            worst = (drop, prev, cur)
    if worst is None:
        return None
    drop, prev, cur = worst
    severity = "alert" if drop >= NDVI_DROP_ALERT else "warn"
    title = "Резкое падение NDVI" + (" (критическое)" if severity == "alert" else "")
    return InsightItem(
        code="ndvi_drop",
        severity=severity,  # type: ignore[arg-type]
        title=title,
        detail=(
            f"NDVI упал с {prev.ndvi_mean:.2f} ({prev.captured_at.isoformat()}) "
            f"до {cur.ndvi_mean:.2f} ({cur.captured_at.isoformat()}): −{drop:.2f} "
            f"между соседними чистыми сценами. Проверьте поле: повреждение "
            "(вредители, болезни, град), ошибку обработки или качество сцены."
        ),
        data_refs=[_scene_ref(prev), _scene_ref(cur)],
    )


def detect_ndvi_stagnation(
    scenes: Sequence[SceneStat], culture_name: str | None = None
) -> InsightItem | None:
    """info: прирост NDVI < 0.05 за 3 чистые сцены в активной вегетации.

    Окно сезона зависит от культуры текущего цикла (09 §8.2 v1.1):
    озимые («озим», «Рожь», «Тритикале») — март–июнь и сентябрь–ноябрь,
    остальные (и поле без цикла) — апрель–октябрь. Плюс средний NDVI трёх
    сцен ≥ 0.3: вне сезона и без вегетации стагнация — норма, детектор
    молчит.
    """
    if len(scenes) < 3:
        return None
    last3 = scenes[-3:]
    months = (
        NDVI_STAGNATION_WINTER_MONTHS
        if _is_winter_culture(culture_name)
        else NDVI_STAGNATION_SEASON_MONTHS
    )
    if last3[-1].captured_at.month not in months:
        return None  # вне сезона вегетации этой культуры
    mean3 = round(sum(s.ndvi_mean for s in last3) / 3, 3)
    if mean3 < NDVI_STAGNATION_MIN_MEAN:
        return None  # вегетации нет
    gain = round(last3[-1].ndvi_mean - last3[0].ndvi_mean, 3)
    if mean3 >= NDVI_STAGNATION_MAX_MEAN or gain >= NDVI_STAGNATION_MAX_GAIN:
        return None
    return InsightItem(
        code="ndvi_stagnation",
        severity="info",
        title="Рост NDVI остановился",
        detail=(
            f"Прирост NDVI за 3 чистые сцены ({last3[0].captured_at.isoformat()} — "
            f"{last3[-1].captured_at.isoformat()}) всего {gain:+.2f} при среднем "
            f"{mean3:.2f} (<{NDVI_STAGNATION_MAX_MEAN}). В фазе активной вегетации рост "
            "должен продолжаться — проверьте питание и влажность почвы."
        ),
        data_refs=[_scene_ref(s) for s in last3],
    )


def detect_waterlogging(
    scenes: Sequence[SceneStat], precip_by_date: Mapping[date, float | None]
) -> InsightItem | None:
    """warn/alert: правило вымокания §7.4 по ВСЕМ чистым сценам сезона (§8.2).

    Эпизод — сцена с waterlogged=true (ndwi_p90 и осадки 7 суток есть);
    выводится при наличии эпизодов, даже если последняя сцена чистая.
    Сцены с waterlogged=null (нет NDWI/погоды) эпизодами не считаются.
    alert — эпизод на последней сцене или ndwi_mean эпизода ≥ порога p90
    (прокси «доли ≥ 30 % пикселей», см. докстринг модуля).
    """
    episodes = [
        scene
        for scene in scenes
        if waterlogged(
            scene.ndwi_p90, scene.ndvi_mean, precip_by_date.get(scene.captured_at)
        )
        is True
    ]
    if not episodes:
        return None

    dates = [scene.captured_at for scene in episodes]  # сцены по возрастанию даты
    max_p90 = max(float(scene.ndwi_p90) for scene in episodes)
    on_latest = bool(scenes) and dates[-1] == scenes[-1].captured_at
    wide = any(
        scene.ndwi_mean is not None and scene.ndwi_mean >= WATERLOG_NDWI_P90
        for scene in episodes
    )

    if len(dates) <= 3:
        dates_str = ", ".join(d.isoformat() for d in dates)
    else:
        n = len(dates)
        ones, tens = n % 10, n % 100
        word = "эпизод" if ones == 1 and tens != 11 else "эпизода" if 2 <= ones <= 4 and not 12 <= tens <= 14 else "эпизодов"
        dates_str = f"с {dates[0].isoformat()} по {dates[-1].isoformat()} — {n} {word}"

    return InsightItem(
        code="waterlogging",
        severity="alert" if (alert := on_latest or wide) else "warn",  # type: ignore[arg-type]
        title="Признаки вымокания поля",
        detail=(
            f"Эпизоды вымокания ({dates_str}): NDWI p90 до {max_p90:.2f} ≥ "
            f"{WATERLOG_NDWI_P90:.2f} при NDVI < {WATERLOG_NDVI_MEAN:.2f} и осадках "
            f"≥ {WATERLOG_PRECIP_MM_7D:.0f} мм за 7 суток"
            + ("; влажных пикселей заметно больше порога" if wide else "")
            + ("; актуально на последней сцене" if on_latest else "")
            + ". Оцените дренаж и отложите полевые работы."
        ),
        data_refs=[
            ref
            for scene in episodes
            for ref in (
                _scene_ref(scene),
                {
                    "source": "weather",
                    "precip_mm_7d": precip_by_date.get(scene.captured_at),
                    "date": scene.captured_at.isoformat(),
                },
            )
        ],
    )


def detect_drought(
    precip_mm_14d: float | None, gdd_14d: float | None, gdd_days: int, day: date
) -> InsightItem | None:
    """warn: осадки 14 сут < 10 мм ∧ GDD выше нормы (допущение — докстринг)."""
    if precip_mm_14d is None or precip_mm_14d >= DROUGHT_PRECIP_MM:
        return None
    if gdd_14d is None or gdd_days < GDD_MIN_DAYS:
        return None
    gdd_daily = gdd_14d / gdd_days
    if gdd_daily <= GDD_NORM_DAILY_C:
        return None
    return InsightItem(
        code="drought",
        severity="warn",
        title="Почвенная засуха",
        detail=(
            f"Осадков за 14 суток (по {day.isoformat()}) {precip_mm_14d:.1f} мм "
            f"(<{DROUGHT_PRECIP_MM:.0f}), средний GDD {gdd_daily:.1f} °C·сут "
            f"(норма {GDD_NORM_DAILY_C:.0f}, база {GDD_BASE_C:.0f} °C) — тепло "
            "усиливает дефицит влаги. Проверьте запасы влаги в почве."
        ),
        data_refs=[
            {
                "source": "weather",
                "date": day.isoformat(),
                "precip_mm_14d": precip_mm_14d,
                "gdd_14d": gdd_14d,
            }
        ],
    )


def detect_ndvi_lag_last_year(scenes: Sequence[SceneStat]) -> InsightItem | None:
    """info: NDVI отстаёт ≥ 0.1 от сцены той же даты прошлого сезона."""
    if len(scenes) < 2:
        return None
    latest = scenes[-1]
    try:
        year_ago = latest.captured_at.replace(year=latest.captured_at.year - 1)
    except ValueError:  # 29 февраля
        year_ago = latest.captured_at.replace(year=latest.captured_at.year - 1, day=28)
    candidates = [
        s for s in scenes if abs((s.captured_at - year_ago).days) <= NDVI_LAG_WINDOW_DAYS
    ]
    if not candidates:
        return None
    ref = min(candidates, key=lambda s: abs((s.captured_at - year_ago).days))
    lag = round(ref.ndvi_mean - latest.ndvi_mean, 3)
    if lag < NDVI_LAG_THRESHOLD:
        return None
    return InsightItem(
        code="ndvi_lag_last_year",
        severity="info",
        title=f"Развитие отстаёт от прошлого сезона на {lag:.2f} NDVI",
        detail=(
            f"NDVI {latest.ndvi_mean:.2f} ({latest.captured_at.isoformat()}) против "
            f"{ref.ndvi_mean:.2f} годом ранее ({ref.captured_at.isoformat()}, та же дата "
            f"±{NDVI_LAG_WINDOW_DAYS} сут). Сравните сроки и нормы внесения с прошлым сезоном."
        ),
        data_refs=[_scene_ref(latest), _scene_ref(ref)],
    )


def detect_agrochem_low(probe: Agrochem | None) -> InsightItem | None:
    """info: pH < 5.5 или гумус < 2 % по последней пробе (REQ-F-031)."""
    if probe is None:
        return None
    reasons = []
    if probe.ph is not None and float(probe.ph) < AGROCHEM_PH_MIN:
        reasons.append(f"pH {float(probe.ph):.1f} < {AGROCHEM_PH_MIN}")
    if probe.humus_pct is not None and float(probe.humus_pct) < AGROCHEM_HUMUS_MIN_PCT:
        reasons.append(f"гумус {float(probe.humus_pct):.1f} % < {AGROCHEM_HUMUS_MIN_PCT:.0f} %")
    if not reasons:
        return None
    return InsightItem(
        code="agrochem_low",
        severity="info",
        title="Низкое почвенное плодородие",
        detail=(
            f"Проба от {probe.sample_date.isoformat()}: {'; '.join(reasons)}. "
            "Спланируйте известкование/внесение органики по результатам обследования."
        ),
        data_refs=[
            {
                "source": "agrochem",
                "probe_id": str(probe.id),
                "sample_date": probe.sample_date.isoformat(),
            }
        ],
    )


def detect_vra_heterogeneity(iqr_ndvi: float | None, day: date | None) -> InsightItem | None:
    """info: IQR NDVI ≥ 0.12 — поле неоднородно, есть зоны для дифвнесения."""
    if iqr_ndvi is None or day is None or iqr_ndvi < VRA_MIN_IQR:
        return None
    return InsightItem(
        code="vra_heterogeneity",
        severity="info",
        title="Неоднородность посева — дифвнесение",
        detail=(
            f"IQR NDVI {iqr_ndvi:.2f} ≥ {VRA_MIN_IQR:.2f} (сцена {day.isoformat()}) — "
            "разброс развития внутри поля значим. Постройте зоны дифференцированного "
            "внесения: раздел «Зоны VRA» карточки поля (GET /fields/{id}/vra)."
        ),
        data_refs=[{"source": "vra", "captured_at": day.isoformat(), "iqr_ndvi": iqr_ndvi}],
    )


__all__ = [
    "AGROCHEM_HUMUS_MIN_PCT",
    "AGROCHEM_PH_MIN",
    "DROUGHT_PRECIP_MM",
    "GDD_BASE_C",
    "GDD_NORM_DAILY_C",
    "NDVI_DROP_ALERT",
    "NDVI_DROP_WARN",
    "SceneStat",
    "detect_agrochem_low",
    "detect_drought",
    "detect_ndvi_drop",
    "detect_ndvi_lag_last_year",
    "detect_ndvi_stagnation",
    "detect_vra_heterogeneity",
    "detect_waterlogging",
    "latest_agrochem",
    "load_scenes",
    "weather_window_14d",
]
