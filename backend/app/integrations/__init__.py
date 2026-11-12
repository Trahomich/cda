"""Слой приёма данных: адаптеры внешних источников (docs/05-integrations.md §1).

Адаптеры отдают нормализованные структуры (05 §3), в доменные таблицы не пишут —
это делает ingest-слой модулей. Общение между адаптерами и модулями — через
типы и ошибки этого пакета (CONTRACTS.md §7).
"""

from dataclasses import dataclass
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
    """Нормализованный факт погоды: сутки × поле (ENT-WEATHER-OBSERVATION, 05 §3).

    cloud_cover_mean_pct / snowfall_cm / snow_depth_cm / precipitation_hours —
    облачность/снег/часы осадков (#126): источник может не отдавать (None).
    """

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
    cloud_cover_mean_pct: float | None = None
    snowfall_cm: float | None = None
    snow_depth_cm: float | None = None
    precipitation_hours: float | None = None
    # #193: почва (посевной слой), ET0, УФ, точка росы, давление (гПа)
    soil_temperature_c: float | None = None
    soil_moisture_pct: float | None = None
    et0_mm: float | None = None
    uv_index: float | None = None
    dew_point_c: float | None = None
    pressure_hpa: float | None = None


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
    precip_prob_pct: int | None = None  # вероятность осадков, % (может не отдаваться)
    hazard: str = "none"  # none | frost | heavy_rain | hail | wind
    source_id: str = ""
    # #193: почва (посевной слой), ET0, УФ, точка росы, давление (гПа)
    soil_temp_c: float | None = None
    soil_moisture_pct: float | None = None
    et0_mm: float | None = None
    uv_index: float | None = None
    dew_point_c: float | None = None
    pressure_hpa: float | None = None


@dataclass(frozen=True, slots=True)
class Scene:
    """Нормализованный снимок: индекс × поле × дата (ENT-SATELLITE-SCENE, 05 §3).

    Статистика индекса — по полезной площади поля (пиксели внутри контура).
    cloud_method — как посчитана облачность (упрощение 05 §2: по метаданным
    сцены, а не по маске облачных пикселей; пометка обязательна).
    """

    field_id: str  # UUID строкой — чтобы не тянуть зависимость от БД
    index: str  # ndvi | ndre | ndmi | savi
    captured_at: date
    resolution_m: int
    index_avg: float
    index_min: float
    index_max: float
    cloud_pct: float
    source_id: str
    trust: float = 1.0  # 1 − cloud_pct/100 при одиночной сцене (05 §5)
    composite_days: int = 1
    anomaly_zones_ref: str | None = None
    scene_ref: str = ""  # идентификатор продукта в каталоге (диагностика)
    cloud_method: str = "scene_metadata"


__all__ = [
    "DataGapError",
    "DataQualityError",
    "ForecastItem",
    "Observation",
    "Scene",
    "SourceCapabilities",
    "SourceUnavailableError",
]
