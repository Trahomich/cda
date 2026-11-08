"""Опасные погодные явления (ОЯ) по прогнозу — #139, docs/06 §7.8/API-07/29.

Пороги зафиксированы константами (обсуждение #429 п.5, согласовано с
Иваном Куликовым на ревью #139). Заморозки опасны при активной вегетации
(культура сезона есть и месяц в её вегетационном окне): вне окна —
info вместо warn/alert. Функции чистые, без БД; БД-зависимая часть —
culture_name в router (детекторы §8.2 current_culture_name).
"""

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.ai_assistant.detectors import (
    NDVI_STAGNATION_SEASON_MONTHS,
    NDVI_STAGNATION_WINTER_MONTHS,
    current_culture_name,
    is_winter_culture,
)

#: frost (заморозки): tmin ≤ +2 °C — warn; ≤ 0 °C и ≤ −5 °C — alert
FROST_WARN_C = 2.0
FROST_ALERT_C = 0.0
FROST_SEVERE_C = -5.0
#: high_wind (сильный ветер): порывы ≥ 15 м/с — warn
HIGH_WIND_MS = 15.0
#: heavy_rain (ливень): осадки ≥ 15 мм/сут — warn
HEAVY_RAIN_MM = 15.0


def _frost_text(tmin: float) -> str:
    if tmin <= FROST_SEVERE_C:
        return f"Сильный мороз: ночью до {tmin:.0f} °C."
    if tmin <= FROST_ALERT_C:
        return f"Заморозки: ночью до {tmin:.0f} °C."
    return f"Похолодание до {tmin:.0f} °C — риск заморозка."  # tmin:6.1f?


def day_hazards(
    day: date,
    tmin: float | None,
    precip_mm: float | None,
    wind_max_ms: float | None,
    culture_name: str | None,
) -> list[dict[str, str]]:
    """ОЯ суток прогноза: [{code, severity, text}] (пусто — явлений нет).

    Заморозки вне активной вегетации (нет культуры сезона или месяц вне
    её окна: обычные — апрель–октябрь, озимые — март–июнь и сентябрь–ноябрь,
    как §8.2 v1.1) — info вместо warn/alert: знать полезно, вреда посевам нет.
    """
    hazards: list[dict[str, str]] = []
    if tmin is not None and tmin <= FROST_WARN_C:
        in_season = culture_name is not None and day.month in (
            NDVI_STAGNATION_WINTER_MONTHS
            if is_winter_culture(culture_name)
            else NDVI_STAGNATION_SEASON_MONTHS
        )
        severity = "info" if not in_season else ("warn" if tmin > FROST_ALERT_C else "alert")
        hazards.append({"code": "frost", "severity": severity, "text": _frost_text(tmin)})
    if wind_max_ms is not None and wind_max_ms >= HIGH_WIND_MS:
        hazards.append(
            {
                "code": "high_wind",
                "severity": "warn",
                "text": f"Сильный ветер: порывы до {wind_max_ms:.0f} м/с.",
            }
        )
    if precip_mm is not None and precip_mm >= HEAVY_RAIN_MM:
        hazards.append(
            {
                "code": "heavy_rain",
                "severity": "warn",
                "text": f"Ливень: до {precip_mm:.0f} мм осадков за сутки.",
            }
        )
    return hazards


def field_hazards(
    db: Session, field_id: uuid.UUID, days: list[tuple[date, object]]
) -> dict[date, list[dict[str, str]]]:
    """hazards по дням прогноза поля: культура сезона — один запрос к БД.

    days — пары (forecast_for, прогноз-объект с tmin/precip/wind атрибутами).
    """
    culture = current_culture_name(db, field_id, date.today())
    return {
        day: day_hazards(
            day, f.temperature_min_c, f.precipitation_mm, f.wind_max_ms, culture
        )
        for day, f in days
    }
