"""Детектор вымокания поля по NDWI (docs/06 §7.4, задача #91).

Правило (§7.4, 09-ai §8.2 «waterlogging»): вымокание фиксируется, если

    ndwi_p90 ≥ 0.10  И  ndvi_mean < 0.45  И  осадки за 7 суток ≥ 20 мм,

где ndwi_p90/ndvi_mean — статистика сцены ndvi (колонки dat_satellite_scene),
осадки — сумма precipitation_mm фактов погоды поля (dat_weather_observation,
IF-WEATHER) за окно 7 суток включая дату сцены. API-06 отдаёт
waterlogged: bool | null (null — данных нет: NDWI не считался или нет
наблюдений погоды в окне).

Константы — точка истины для детектора ai_assistant (09-ai §8.2, задача #98):
импорт отсюда, дублирование чисел не допускается.
"""

from collections.abc import Iterable
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integrations import WeatherObservation

#: пороги правила вымокания (docs/06 §7.4)
WATERLOG_NDWI_P90 = 0.10
WATERLOG_NDVI_MEAN = 0.45
WATERLOG_PRECIP_MM_7D = 20.0

#: окно осадков: дата сцены − 6 суток … дата сцены (7 суток включительно)
PRECIP_WINDOW_DAYS = 7  # #204: 7 завершённых суток [day−7, day−1]


def waterlogged(
    ndwi_p90: float | None,
    ndvi_mean: float | None,
    precip_mm_7d: float | None,
) -> bool | None:
    """Флаг вымокания по правилу §7.4; None — данных нет."""
    if ndwi_p90 is None or precip_mm_7d is None:
        return None
    return (
        ndwi_p90 >= WATERLOG_NDWI_P90
        and ndvi_mean is not None
        and ndvi_mean < WATERLOG_NDVI_MEAN
        and precip_mm_7d >= WATERLOG_PRECIP_MM_7D
    )


def precip_sum_completed(
    db: Session, field_id, day: date, *, days: int = PRECIP_WINDOW_DAYS
) -> float | None:
    """Единая сумма осадков за ЗАВЕРШЁННЫЕ сутки (мм), #204: окно
    [day − days, day − 1] — 7/14 завершённых суток, без самих суток day.

    Единая точка расчёта «осадков за N суток» (карта API-61, сводка
    API-07, детекторы/ИИ-контекст): NULL precipitation_mm считается нулём
    (день без осадков); дубль (поле, дата) — большая trust; None —
    наблюдений в окне нет.
    """
    rows = db.execute(
        select(
            WeatherObservation.observed_at,
            WeatherObservation.precipitation_mm,
            WeatherObservation.trust,
        ).where(
            WeatherObservation.field_id == field_id,
            WeatherObservation.observed_at >= day - timedelta(days=days),
            WeatherObservation.observed_at <= day - timedelta(days=1),
        )
    ).all()
    by_date: dict[date, float] = {}
    trust_by_date: dict[date, float] = {}
    for observed_at, mm, trust in rows:
        value = float(mm) if mm is not None else 0.0
        if observed_at not in by_date or float(trust) > trust_by_date[observed_at]:
            by_date[observed_at] = value
            trust_by_date[observed_at] = float(trust)
    if not by_date:
        return None
    return round(sum(by_date.values()), 1)


def precip_sums_7d(db: Session, field_id, dates: Iterable[date]) -> dict[date, float | None]:
    """Сумма осадков за 7 завершённых суток ДО каждой даты (мм), #204.

    Окно [day−7, day−1] — та же precip_sum_completed: единое число с
    картой API-61 и сводкой API-07. None — наблюдений в окне нет
    (данных нет → waterlogged=null).
    """
    unique = sorted(set(dates))
    return {day: precip_sum_completed(db, field_id, day) for day in unique}


__all__ = [
    "PRECIP_WINDOW_DAYS",
    "WATERLOG_NDWI_P90",
    "WATERLOG_NDVI_MEAN",
    "WATERLOG_PRECIP_MM_7D",
    "precip_sum_completed",
    "precip_sums_7d",
    "waterlogged",
]
