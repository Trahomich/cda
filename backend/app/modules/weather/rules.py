"""Погодные окна операций RULE-AGRO-09 (docs/03-domain-agronomy.md §3.3).

Пороги хранятся в ref_weather_rule (params JSONB, UQ operation_type) и
сидируются идемпотентно (CLI ``seed-weather-rules``); расчёт окна — чистая
функция ``evaluate_day`` по параметрам правила.

Маппинги суточных прогнозов Open-Meteo к формулировкам правил (документируются
в rule_text для агронома):
- «вероятность осадков < 50 %» -> суточная сумма осадков < precip_mm
  (spraying: 0.5 мм — при суточной агрегации эквивалент «осадков практически
  не ожидается»; docs/05 §5 не отдаёт вероятности на суточном уровне);
- «осадков за 2 ч после обработки не ожидается» -> входит в тот же порог 0.5 мм;
- «осадков не ожидается в течение 6 ч» -> суточная сумма < 1 мм (fertilizing);
- «T почвы ≥ 4 °C» -> прокси Tср воздуха ≥ 4 °C (Open-Meteo не даёт T почвы);
- Tср = (t_max + t_min) / 2.
"""

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.references import WeatherRule

# Параметры (ключи params JSONB) и пороги по умолчанию — RULE-AGRO-09.
# Ключи: t_avg_min/t_avg_max (°C), wind_max (м/с), precip_max (мм за сутки),
# humidity_min/humidity_max (%), prev_precip_max (мм за предыдущие сутки).
DEFAULT_WEATHER_RULES: list[dict] = [
    {
        "operation_type": "spraying",
        "params": {
            "t_avg_min": 5.0,
            "t_avg_max": 25.0,
            "wind_max": 4.0,
            "precip_max": 0.5,
            "humidity_min": 40.0,
            "humidity_max": 80.0,
        },
        "rule_text": (
            "Опрыскивание: 5 °C ≤ Tср ≤ 25 °C, ветер ≤ 4 м/с, осадки < 0.5 мм/сут "
            "(эквивалент вероятности < 50 % и «без осадков 2 ч»), влажность 40–80 %. "
            "Tср = (t_max + t_min) / 2."
        ),
    },
    {
        "operation_type": "fertilizing",
        "params": {"t_avg_min": 5.0, "wind_max": 6.0, "precip_max": 1.0},
        "rule_text": (
            "Внесение жидких удобрений: Tср ≥ 5 °C, ветер ≤ 6 м/с, осадки < 1 мм/сут "
            "(эквивалент «без осадков в течение 6 ч»). Tср = (t_max + t_min) / 2."
        ),
    },
    {
        "operation_type": "seeding",
        "params": {"t_avg_min": 4.0, "prev_precip_max": 10.0},
        "rule_text": (
            "Посев ранних яровых: T почвы ≥ 4 °C (прокси: Tср воздуха ≥ 4 °C), "
            "осадки за предыдущие сутки < 10 мм. Tср = (t_max + t_min) / 2."
        ),
    },
    {
        "operation_type": "harvesting",
        "params": {"precip_max": 2.0, "humidity_max": 90.0},
        "rule_text": "Уборка: осадки за сутки < 2 мм, влажность воздуха < 90 %.",
    },
]

OPERATION_TYPES = tuple(r["operation_type"] for r in DEFAULT_WEATHER_RULES)


def seed_weather_rules(db: Session) -> int:
    """Идемпотентно актуализировать ref_weather_rule (upsert по operation_type).

    Возвращает число изменённых строк (insert + update).
    """
    statement = pg_insert(WeatherRule).values(DEFAULT_WEATHER_RULES)
    statement = statement.on_conflict_do_update(
        index_elements=["operation_type"],
        set_={
            "params": statement.excluded.params,
            "rule_text": statement.excluded.rule_text,
            "updated_at": func.now(),
        },
    )
    result = db.execute(statement)
    changed = result.rowcount or 0
    db.commit()
    return changed


@dataclass(frozen=True, slots=True)
class DayMetrics:
    """Суточные метрики для оценки окна (прогноз ENT-FORECAST или факт)."""

    day: str  # ISO-дата (в ответе API — строка)
    temperature_max_c: float | None
    temperature_min_c: float | None
    precipitation_mm: float | None
    wind_max_ms: float | None
    humidity_avg_pct: float | None


@dataclass(frozen=True, slots=True)
class WindowDay:
    """Итог оценки суток: входят ли в погодное окно и почему нет."""

    day: str
    ok: bool
    reasons: list[str]


def evaluate_day(rule_params: dict, m: DayMetrics, prev_precip: float | None) -> WindowDay:
    """Оценить одни сутки по параметрам правила; причины — на русском (RULE-AGRO-09)."""
    reasons: list[str] = []
    t_avg = (
        None
        if m.temperature_max_c is None or m.temperature_min_c is None
        else (m.temperature_max_c + m.temperature_min_c) / 2
    )

    if "t_avg_min" in rule_params:
        threshold = float(rule_params["t_avg_min"])
        if t_avg is None:
            reasons.append("нет данных по температуре")
        elif t_avg < threshold:
            reasons.append(f"Tср {t_avg:.0f} °C < {threshold:.0f} °C")

    if "t_avg_max" in rule_params:
        threshold = float(rule_params["t_avg_max"])
        if t_avg is not None and t_avg > threshold:
            reasons.append(f"Tср {t_avg:.0f} °C > {threshold:.0f} °C")

    if "wind_max" in rule_params:
        threshold = float(rule_params["wind_max"])
        if m.wind_max_ms is None:
            reasons.append("нет данных по ветру")
        elif m.wind_max_ms > threshold:
            reasons.append(f"ветер {m.wind_max_ms:.0f} м/с > {threshold:.0f} м/с")

    if "precip_max" in rule_params:
        threshold = float(rule_params["precip_max"])
        if m.precipitation_mm is None:
            reasons.append("нет данных по осадкам")
        elif m.precipitation_mm >= threshold:
            reasons.append(f"осадки {m.precipitation_mm:.1f} мм ≥ {threshold:g} мм")

    if "humidity_min" in rule_params:
        threshold = float(rule_params["humidity_min"])
        if m.humidity_avg_pct is None:
            reasons.append("нет данных по влажности")
        elif m.humidity_avg_pct < threshold:
            reasons.append(f"влажность {m.humidity_avg_pct:.0f} % < {threshold:.0f} %")

    if "humidity_max" in rule_params:
        threshold = float(rule_params["humidity_max"])
        if m.humidity_avg_pct is None:
            pass  # уже добавлено в humidity_min (правило только у spraying/harvesting)
        elif m.humidity_avg_pct >= threshold:
            reasons.append(f"влажность {m.humidity_avg_pct:.0f} % ≥ {threshold:.0f} %")

    if "prev_precip_max" in rule_params:
        threshold = float(rule_params["prev_precip_max"])
        if prev_precip is None:
            reasons.append("нет данных по осадкам за предыдущие сутки")
        elif prev_precip >= threshold:
            reasons.append(f"осадки за пред. сутки {prev_precip:.1f} мм ≥ {threshold:g} мм")

    return WindowDay(day=m.day, ok=not reasons, reasons=reasons)


def evaluate_window(
    rule_params: dict,
    days: list[DayMetrics],
    prev_day_precip: float | None,
) -> list[WindowDay]:
    """Оценить горизонт суток; prev_day_precip — осадки за сутки до первого дня."""
    return [evaluate_day(rule_params, m, prev_day_precip) for m in days]


__all__ = [
    "DEFAULT_WEATHER_RULES",
    "DayMetrics",
    "OPERATION_TYPES",
    "WindowDay",
    "evaluate_day",
    "evaluate_window",
    "seed_weather_rules",
]
