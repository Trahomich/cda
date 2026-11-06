"""Погодное окно для планов spraying (WEATHER-WINDOW, docs/06 §7.2, задача #74).

Отличается от GET /weather/windows (RULE-AGRO-09, ref_weather_rule):
окно здесь — НЕблокирующая подсказка в ответе POST /treatment-plans (API-15)
и approve (API-16), с жёсткими порогами контракта §7.2, без влажности
(провайдер её не отдаёт):

- 5 °C ≤ Tср ≤ 25 °C, где Tср = (t_min + t_max) / 2 по суткам;
- ветер ≤ 4 м/с;
- precip_prob_pct < 50.

Допущения (зафиксированы по §7.2):
- источник — только кэш dat_forecast (сетевых вызовов в момент запроса нет);
- если записи прогноза на planned_date нет — окно null (не ошибка);
- метрика с null в прогнозе не проверяется (провайдер может не отдавать
  значение — §7.2 требует tolerировать null); сутки, где ни одна метрика
  не нарушена, считаются подходящими;
- suggested_dates — до 5 дат из ближайших 7 суток, начиная с planned_date
  (включительно), по возрастанию, где все критерии выполняются.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integrations import Forecast

T_AVG_MIN_C = 5.0
T_AVG_MAX_C = 25.0
WIND_MAX_MS = 4.0
PRECIP_PROB_MAX_PCT = 50

SUGGEST_HORIZON_DAYS = 7
SUGGEST_MAX_DATES = 5


@dataclass(frozen=True, slots=True)
class WeatherWindowResult:
    """WEATHER-WINDOW (§7.2): ok/detail на planned_date + альтернативы."""

    ok: bool
    detail: str
    suggested_dates: list[date]


def _day_reasons(fc: Forecast) -> list[str]:
    """Нарушенные критерии §7.2 по одним суткам прогноза (формат из примера
    контракта: «ветер 6.2 м/с (>4), осадки 70 % (≥50)»); null — не проверяем."""
    reasons: list[str] = []
    t_avg: float | None = None
    if fc.temperature_min_c is not None and fc.temperature_max_c is not None:
        t_avg = (float(fc.temperature_min_c) + float(fc.temperature_max_c)) / 2
    if t_avg is not None and not (T_AVG_MIN_C <= t_avg <= T_AVG_MAX_C):
        reasons.append(f"Tср {t_avg:.1f} °C (вне {T_AVG_MIN_C:.0f}–{T_AVG_MAX_C:.0f})")
    if fc.wind_max_ms is not None and float(fc.wind_max_ms) > WIND_MAX_MS:
        reasons.append(f"ветер {float(fc.wind_max_ms):.1f} м/с (>{WIND_MAX_MS:.0f})")
    if fc.precip_prob_pct is not None and int(fc.precip_prob_pct) >= PRECIP_PROB_MAX_PCT:
        reasons.append(f"осадки {int(fc.precip_prob_pct)} % (≥{PRECIP_PROB_MAX_PCT})")
    return reasons


def weather_window_for_plan(
    db: Session, *, field_id: uuid.UUID, planned_date: date
) -> WeatherWindowResult | None:
    """Окно для плана spraying по кэшу прогноза поля.

    None — если прогноза на planned_date нет (weather_window: null в ответе).
    """
    on_date = db.scalar(
        select(Forecast).where(Forecast.field_id == field_id, Forecast.forecast_for == planned_date)
    )
    if on_date is None:
        return None

    reasons = _day_reasons(on_date)
    detail = (
        ""
        if not reasons
        else f"{planned_date.isoformat()}: " + ", ".join(reasons)
    )

    horizon = db.scalars(
        select(Forecast)
        .where(
            Forecast.field_id == field_id,
            Forecast.forecast_for >= planned_date,
            Forecast.forecast_for <= planned_date + timedelta(days=SUGGEST_HORIZON_DAYS - 1),
        )
        .order_by(Forecast.forecast_for)
    ).all()
    suggested = [fc.forecast_for for fc in horizon if not _day_reasons(fc)][:SUGGEST_MAX_DATES]

    return WeatherWindowResult(ok=not reasons, detail=detail, suggested_dates=suggested)


__all__ = [
    "PRECIP_PROB_MAX_PCT",
    "SUGGEST_HORIZON_DAYS",
    "SUGGEST_MAX_DATES",
    "T_AVG_MAX_C",
    "T_AVG_MIN_C",
    "WIND_MAX_MS",
    "WeatherWindowResult",
    "weather_window_for_plan",
]
