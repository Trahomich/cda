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

from app.models.integrations import Forecast

#: окно вегетации для оценки опасности заморозка (#139): как детекторы
#: §8.2 v1.1 (app/ai_assistant/detectors.py) — дублируется здесь, чтобы не
#: заводить цикл импорта weather → ai_assistant → weather
VEGETATION_SEASON_MONTHS = frozenset(range(4, 11))  # обычные — апрель–октябрь
VEGETATION_WINTER_MONTHS = frozenset({3, 4, 5, 6, 9, 10, 11})  # озимые
WINTER_CULTURE_MARKERS = ("озим", "рожь", "тритикале")

#: frost (заморозки): tmin ≤ +2 °C — warn; ≤ 0 °C и ≤ −5 °C — alert
FROST_WARN_C = 2.0
FROST_ALERT_C = 0.0
FROST_SEVERE_C = -5.0
#: high_wind (сильный ветер): порывы ≥ 15 м/с — warn
HIGH_WIND_MS = 15.0
#: heavy_rain (ливень): осадки ≥ 15 мм/сут — warn
HEAVY_RAIN_MM = 15.0


def _winter_culture(culture_name: str) -> bool:
    """Маркер озимой в имени культуры — как детекторы §8.2 v1.1."""
    lowered = culture_name.lower()
    return any(marker in lowered for marker in WINTER_CULTURE_MARKERS)


def _frost_text(tmin: float) -> str:
    if tmin <= FROST_SEVERE_C:
        return f"Сильный мороз: ночью до {tmin:.0f} °C."
    if tmin <= FROST_ALERT_C:
        return f"Заморозки: ночью до {tmin:.0f} °C."
    return f"Похолодание до {tmin:.0f} °C — риск заморозка."


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
            if _winter_culture(culture_name)
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


def forecast_hazards(
    db: Session, field_id: uuid.UUID, forecasts: list[Forecast]
) -> list[list[dict[str, str]]]:
    """hazards для каждого элемента прогноза (по порядку forecasts).

    Культура сезона — один запрос к БД (§8.2 current_culture_name).
    """
    culture = current_culture_name(db, field_id, date.today())
    return [
        day_hazards(f.forecast_for, f.temperature_min_c, f.precipitation_mm, f.wind_max_ms, culture)
        for f in forecasts
    ]
