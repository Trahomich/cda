"""Pydantic-схемы модуля погоды (API-07/29, окна RULE-AGRO-09)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class FactItem(BaseModel):
    """Суточный факт погоды (ENT-WEATHER-OBSERVATION); облачность/снег — #126."""

    model_config = ConfigDict(from_attributes=True)

    observed_at: date
    temperature_avg_c: float | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    precipitation_mm: float | None
    wind_max_ms: float | None
    humidity_avg_pct: float | None
    cloud_cover_mean_pct: float | None = Field(
        default=None, description="Средняя облачность, % (#126); null — источник не отдаёт"
    )
    snowfall_cm: float | None = Field(
        default=None, description="Сумма снегопада за сутки, см (#126)"
    )
    snow_depth_cm: float | None = Field(
        default=None, description="Максимальная высота снежного покрова, см (#126)"
    )
    precipitation_hours: float | None = Field(
        default=None, description="Часы с осадками за сутки, ч (#126)"
    )
    source_id: str


class ForecastDayItem(BaseModel):
    """Прогноз на сутки (ENT-FORECAST, последняя версия)."""

    model_config = ConfigDict(from_attributes=True)

    forecast_for: date
    issued_at: datetime
    temperature_min_c: float | None
    temperature_max_c: float | None
    precipitation_mm: float | None
    wind_max_ms: float | None
    humidity_avg_pct: float | None
    precip_prob_pct: int | None = Field(
        default=None, description="Вероятность осадков, %; null — провайдер не отдаёт (§7.2)"
    )
    hazard: str


class SourceMeta(BaseModel):
    """Состояние погодного адаптера (REQ-F-034, API-28)."""

    source_id: str
    status: str | None
    last_success_at: datetime | None
    stale: bool = Field(description="Прогноз старше 12 ч — данные могли устареть (05 §5)")


class FieldWeatherResponse(BaseModel):
    """API-07: факты + прогноз 7 сут по полю."""

    field_id: uuid.UUID
    facts: list[FactItem]
    forecast: list[ForecastDayItem]
    meta: SourceMeta


class ForecastListResponse(BaseModel):
    """API-29: прогноз по полю (обёртка items/total/limit/offset)."""

    items: list[ForecastDayItem]
    total: int
    limit: int
    offset: int
    meta: SourceMeta


class HistoryListResponse(BaseModel):
    """API: история фактов погоды по полю (карточка погоды, #126)."""

    items: list[FactItem]
    total: int
    limit: int
    offset: int
    meta: SourceMeta


class GddDayItem(BaseModel):
    """День GDD-кривой (API-58, §7.8, #132)."""

    date: date
    gdd_day: float | None = Field(
        description="Эффективные температуры суток, °C·сут: "
        "max(0, (tmax+tmin)/2 − base); null — нет tmin или tmax, в сумму не входит"
    )
    gdd_cum: float = Field(description="Накоплено с 1 января года (включая этот день)")


class GddNormItem(BaseModel):
    """Точка многолетней нормы GDD на календарную дату (API-58, §7.8)."""

    date: date
    norm_cum: float | None = Field(
        description="Среднее gdd_cum прошлых лет на эту дату (день+месяц); "
        "null — даты нет в годах истории"
    )


class GddResponse(BaseModel):
    """API-58: накопительная GDD-кривая поля с 1 января года (§7.8, #132)."""

    field_id: uuid.UUID
    year: int
    base_c: float = Field(description="База эффективных температур, °C")
    total: float = Field(description="gdd_cum последнего дня с наблюдениями (0 — данных нет)")
    items: list[GddDayItem]
    norm_cum: list[GddNormItem] | None = Field(
        default=None,
        description="Линия многолетней нормы по тем же датам; null — прошлых лет < 2",
    )


class WindowDayItem(BaseModel):
    """Сутки горизонта: входят ли в погодное окно операции и почему нет."""

    day: date
    ok: bool
    reasons: list[str]


class WindowResponse(BaseModel):
    """Погодное окно операции (RULE-AGRO-09)."""

    operation_type: str
    field_id: uuid.UUID
    rule_text: str
    window_open: bool = Field(description="Есть ли хотя бы одни подходящие сутки")
    days: list[WindowDayItem]
    alert: str | None = Field(
        default=None, description="Текст для ENT-ALERT (warning), если окна нет (RULE-AGRO-09)"
    )


__all__ = [
    "FactItem",
    "FieldWeatherResponse",
    "ForecastDayItem",
    "ForecastListResponse",
    "HistoryListResponse",
    "SourceMeta",
    "WindowDayItem",
    "WindowResponse",
]
