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
PRECIP_WINDOW_DAYS = 6


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


def precip_sums_7d(db: Session, field_id, dates: Iterable[date]) -> dict[date, float | None]:
    """Сумма осадков за 7 суток до каждой даты (мм) по фактам погоды поля.

    None — наблюдений в окне нет (данных нет → waterlogged=null). NULL
    precipitation_mm у имеющихся наблюдений считается нулём (день без
    осадков у провайдера); дедупликация — UQ(field_id, observed_at, source_id).
    """
    unique = sorted(set(dates))
    if not unique:
        return {}
    rows = db.execute(
        select(
            WeatherObservation.observed_at,
            WeatherObservation.precipitation_mm,
        ).where(
            WeatherObservation.field_id == field_id,
            WeatherObservation.observed_at >= unique[0] - timedelta(days=PRECIP_WINDOW_DAYS),
            WeatherObservation.observed_at <= unique[-1],
        )
    ).all()
    by_date: dict[date, list[float]] = {}
    for observed_at, mm in rows:
        by_date.setdefault(observed_at, []).append(float(mm) if mm is not None else 0.0)

    result: dict[date, float | None] = {}
    for day in unique:
        values = [
            v
            for back in range(PRECIP_WINDOW_DAYS + 1)
            for v in by_date.get(day - timedelta(days=back), [])
        ]
        result[day] = round(sum(values), 1) if values else None
    return result


__all__ = [
    "PRECIP_WINDOW_DAYS",
    "WATERLOG_NDWI_P90",
    "WATERLOG_NDVI_MEAN",
    "WATERLOG_PRECIP_MM_7D",
    "precip_sums_7d",
    "waterlogged",
]
