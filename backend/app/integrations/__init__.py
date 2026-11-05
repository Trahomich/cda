"""Слой приёма данных: адаптеры внешних источников (docs/05-integrations.md §1).

Адаптеры отдают нормализованные структуры (05 §3), в доменные таблицы не пишут —
это делает ingest-слой модулей. Общение между адаптерами и модулями — через
типы и ошибки этого пакета (CONTRACTS.md §7).
"""

from dataclasses import dataclass, field
from datetime import date, datetime


class SourceUnavailableError(Exception):
    """Источник не отвечает (сеть/5xx/таймаут) — ретраи по расписанию (05 §6)."""


class DataGapError(Exception):
    """Нет данных за период — нормальное состояние, не ошибка (05 §6)."""


class DataQualityError(Exception):
    """Данные пришли, но не прошли валидацию (05 §5) — запись отбрасывается."""


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    """Возможности адаптера (05 §2)."""

    source_id: str
    kind: str
    history: bool = False
    current: bool = False
    forecast_days: int = 0
    metrics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Observation:
    """Нормализованный факт погоды: сутки × поле (ENT-WEATHER-OBSERVATION, 05 §3)."""

    field_id: str  # UUID строкой — чтобы не тянуть зависимость от БД
    observed_at: date
    temperature_avg_c: float | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    precipitation_mm: float | None
    wind_max_ms: float | None
    humidity_avg_pct: float | None
    source_id: str
    trust: float = 1.0


@dataclass(frozen=True, slots=True)
class ForecastItem:
    """Нормализованный прогноз на сутки (ENT-FORECAST, 05 §3)."""

    field_id: str
    forecast_for: date
    issued_at: datetime
    temperature_min_c: float | None
    temperature_max_c: float | None
    precipitation_mm: float | None
    wind_max_ms: float | None
    humidity_avg_pct: float | None
    hazard: str = "none"  # none | frost | heavy_rain | hail | wind
    source_id: str = ""


__all__ = [
    "DataGapError",
    "DataQualityError",
    "ForecastItem",
    "Observation",
    "SourceCapabilities",
    "SourceUnavailableError",
]
