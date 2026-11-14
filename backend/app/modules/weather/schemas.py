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
    # #193: почва, ET0, УФ, точка росы, давление
    soil_temperature_c: float | None = Field(
        default=None, description="Температура почвы (посевной слой), °C"
    )
    soil_moisture_pct: float | None = Field(
        default=None, description="Объёмная влажность почвы посевного слоя, %"
    )
    et0_mm: float | None = Field(default=None, description="Эвапотранспирация ET0, мм/сут")
    uv_index: float | None = Field(default=None, description="УФ-индекс (максимум суток)")
    dew_point_c: float | None = Field(default=None, description="Точка росы, °C")
    pressure_hpa: float | None = Field(default=None, description="Давление (уровень моря), гПа")
    norm_mm: float | None = Field(
        default=None,
        description="Многолетняя норма осадков на эту календарную дату (среднее "
        "по полным прошлым годам, §7.8); null — истории < 2 лет",
    )
    source_id: str


class HazardItem(BaseModel):
    """Опасное погодное явление суток прогноза (#139, §7.8).

    code: frost | high_wind | heavy_rain; severity: заморозки warn при
    tmin ≤ +2 °C, alert при ≤ 0 °C (вне активной вегетации — info);
    ветер ≥ 15 м/с и ливень ≥ 15 мм/сут — warn.
    """

    code: str = Field(description="frost | high_wind | heavy_rain")
    severity: str = Field(description="info | warn | alert")
    text: str = Field(description="Краткое описание явления для пользователя")


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
    # #193: почва, ET0, УФ, точка росы, давление (имена согласованы с фронт #577)
    soil_temp_c: float | None = Field(
        default=None, description="Температура почвы (посевной слой), °C"
    )
    soil_moisture_pct: float | None = Field(
        default=None, description="Объёмная влажность почвы посевного слоя, %"
    )
    et0_mm: float | None = Field(default=None, description="Эвапотранспирация ET0, мм/сут")
    uv_index: float | None = Field(default=None, description="УФ-индекс (максимум суток)")
    dew_point_c: float | None = Field(default=None, description="Точка росы, °C")
    pressure_hpa: float | None = Field(default=None, description="Давление (уровень моря), гПа")
    hazard: str
    hazards: list[HazardItem] = Field(
        default_factory=list,
        description="Опасные явления суток (#139): пусто — явлений нет",
    )


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
    #: #204: единое окно завершённых суток [T−N, T−1] — то же число,
    #: что на карте API-61 и в детекторах; null — наблюдений в окне нет
    precip_7d: float | None = Field(
        default=None, description="Осадки за 7 завершённых суток, мм (как API-61)"
    )
    precip_14d: float | None = Field(
        default=None, description="Осадки за 14 завершённых суток, мм (как API-61)"
    )
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
    nearest_window_date: date | None = Field(
        default=None,
        description="Дата первых подходящих суток горизонта или null (§7.8, API-59)",
    )
    days: list[WindowDayItem]
    alert: str | None = Field(
        default=None, description="Текст для ENT-ALERT (warning), если окна нет (RULE-AGRO-09)"
    )


class WindowsListResponse(BaseModel):
    """Список погодных окон по типам операций поля (API-59, §7.8).

    ``only_planned=true`` — только типы из незавершённых планов внесения
    текущего сезона; планов нет → ``items=[]`` и ``hint`` (не ошибка).
    """

    field_id: uuid.UUID
    only_planned: bool
    items: list[WindowResponse]
    hint: str | None = None


class WeatherMapItem(BaseModel):
    """Поле на карте погоды (API-61, §7.8): контур + значение метрики."""

    field_id: uuid.UUID
    name: str
    contour: dict = Field(description="Упрощённый контур поля (GeoJSON Polygon)")
    value: float | None = Field(default=None, description="Значение метрики; null — нет наблюдений")
    updated_at: date | None = Field(
        default=None, description="Дата последнего наблюдения поля (сутки)"
    )


class WeatherMapResponse(BaseModel):
    """Карта погоды хозяйства (API-61): заливка полей по метрике."""

    metric: str = Field(description="precip_7d | precip_14d | t_now | gdd_ytd")
    items: list[WeatherMapItem]


__all__ = [
    "FactItem",
    "FieldWeatherResponse",
    "ForecastDayItem",
    "ForecastListResponse",
    "GddDayItem",
    "GddNormItem",
    "GddResponse",
    "HistoryListResponse",
    "SourceMeta",
    "WeatherMapItem",
    "WeatherMapResponse",
    "WindowDayItem",
    "WindowResponse",
    "WindowsListResponse",
]
