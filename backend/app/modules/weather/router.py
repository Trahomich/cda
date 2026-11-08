"""API модуля погоды: API-07, API-29, погодные окна RULE-AGRO-09 (MVP-6).

Эндпоинты (docs/06-data-api.md §5):
- API-07 GET /fields/{field_id}/weather — факты (последние 14 сут) + прогноз 7 сут
  + meta источника (статус адаптера, stale-метка по issued_at > 12 ч, 05 §5);
- API-29 GET /weather/forecast?field_id&days — прогноз (кэш 3 ч на приёме);
- GET /weather/windows?operation_type&field_id — окно операции по ref_weather_rule.

Права (07-ux §5): группа «weather» — все 5 ролей (E-06, E-10).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.models.agronomy import Field
from app.models.integrations import Forecast, WeatherObservation
from app.models.references import Source, WeatherRule
from app.modules.weather import ingest as ingest_module
from app.modules.weather.rules import (
    OPERATION_TYPES,
    DayMetrics,
    evaluate_window,
)
from app.modules.weather.schemas import (
    FactItem,
    FieldWeatherResponse,
    ForecastDayItem,
    ForecastListResponse,
    HistoryListResponse,
    SourceMeta,
    WindowDayItem,
    WindowResponse,
)

DbSession = Annotated[Session, Depends(get_db)]
WEATHER_SOURCE_ID = "weather.open-meteo"
FACT_DAYS = 14  # глубина фактов в API-07 (прогноз — 7 сут по контракту)

router = APIRouter(tags=["weather"], dependencies=[Depends(group_checker("weather"))])


def _get_field(db: Session, field_id: uuid.UUID) -> Field:
    field = db.get(Field, field_id)
    if field is None:
        raise AppError(
            f"Поле {field_id} не найдено",
            code="field_not_found",
            title="Не найдено",
            status_code=404,
        )
    return field


def _source_meta(db: Session, forecasts: list[Forecast]) -> SourceMeta:
    """Мета источника: ref_source + stale по возрасту issued_at последнего прогноза."""
    source = db.scalar(select(Source).where(Source.source_id == WEATHER_SOURCE_ID))
    stale = False
    if forecasts:
        newest = max(f.issued_at for f in forecasts)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        stale = datetime.now(tz=UTC) - newest > timedelta(hours=ingest_module.STALE_FORECAST_HOURS)
    return SourceMeta(
        source_id=WEATHER_SOURCE_ID,
        status=source.last_status if source else None,
        last_success_at=source.last_success_at if source else None,
        stale=stale,
    )


def _forecast_days(db: Session, field_id: uuid.UUID, days: int) -> list[Forecast]:
    """Прогноз поля начиная с сегодня, от младших суток к старшим."""
    return list(
        db.scalars(
            select(Forecast)
            .where(Forecast.field_id == field_id, Forecast.forecast_for >= date.today())
            .order_by(Forecast.forecast_for)
            .limit(days)
        )
    )


@router.get(
    "/fields/{field_id}/weather",
    response_model=FieldWeatherResponse,
    summary="API-07: погода по полю — факты и прогноз 7 сут",
)
def field_weather(field_id: uuid.UUID, db: DbSession) -> FieldWeatherResponse:
    field = _get_field(db, field_id)
    facts = list(
        db.scalars(
            select(WeatherObservation)
            .where(
                WeatherObservation.field_id == field.id,
                WeatherObservation.observed_at >= date.today() - timedelta(days=FACT_DAYS),
            )
            .order_by(WeatherObservation.observed_at)
        )
    )
    forecasts = _forecast_days(db, field.id, days=7)
    return FieldWeatherResponse(
        field_id=field.id,
        facts=[FactItem.model_validate(f) for f in facts],
        forecast=[ForecastDayItem.model_validate(f) for f in forecasts],
        meta=_source_meta(db, forecasts),
    )


@router.get(
    "/weather/forecast",
    response_model=ForecastListResponse,
    summary="API-29: прогноз по полю (горизонт до 7 сут, кэш 3 ч на приёме)",
)
def weather_forecast(
    db: DbSession,
    *,
    field_id: uuid.UUID = Query(description="UUID поля"),
    days: int = Query(default=7, ge=1, le=7, description="Горизонт, суток (контракт: 7)"),
    offset: int = Query(default=0, ge=0),
) -> ForecastListResponse:
    field = _get_field(db, field_id)
    # полный горизонт (total) без limit, окно — offset..offset+days
    forecasts = list(
        db.scalars(
            select(Forecast)
            .where(Forecast.field_id == field.id, Forecast.forecast_for >= date.today())
            .order_by(Forecast.forecast_for)
        )
    )
    window = forecasts[offset : offset + days]
    return ForecastListResponse(
        items=[ForecastDayItem.model_validate(f) for f in window],
        total=len(forecasts),
        limit=days,
        offset=offset,
        meta=_source_meta(db, forecasts),
    )


@router.get(
    "/weather/history",
    response_model=HistoryListResponse,
    summary="История фактов погоды по полю (карточка погоды, #126)",
)
def weather_history(
    db: DbSession,
    *,
    field_id: uuid.UUID = Query(description="UUID поля"),
    date_from: date = Query(description="Начало периода, ISO-дата"),
    date_to: date = Query(description="Конец периода, ISO-дата (включительно)"),
    limit: int = Query(default=366, ge=1, le=1500, description="Суток на странице"),
    offset: int = Query(default=0, ge=0),
) -> HistoryListResponse:
    """Ежедневные факты (включая облачность/снег, #126) с пагинацией по offset."""
    field = _get_field(db, field_id)
    if date_from > date_to:
        raise AppError(
            "date_from позже date_to",
            code="invalid_period",
            title="Некорректный запрос",
            status_code=422,
        )
    rows = list(
        db.scalars(
            select(WeatherObservation)
            .where(
                WeatherObservation.field_id == field.id,
                WeatherObservation.observed_at >= date_from,
                WeatherObservation.observed_at <= date_to,
            )
            .order_by(WeatherObservation.observed_at)
        )
    )
    window = rows[offset : offset + limit]
    return HistoryListResponse(
        items=[FactItem.model_validate(o) for o in window],
        total=len(rows),
        limit=limit,
        offset=offset,
        meta=_source_meta(db, forecasts=[]),
    )


@router.get(
    "/weather/windows",
    response_model=WindowResponse,
    summary="Погодное окно операции (RULE-AGRO-09, docs/03 §3.3)",
)
def weather_window(
    db: DbSession,
    *,
    operation_type: str = Query(description=f"Тип операции: {', '.join(OPERATION_TYPES)}"),
    field_id: uuid.UUID = Query(description="UUID поля"),
) -> WindowResponse:
    field = _get_field(db, field_id)
    if operation_type not in OPERATION_TYPES:
        raise AppError(
            f"Неизвестный тип операции «{operation_type}»; допустимо: {', '.join(OPERATION_TYPES)}",
            code="unknown_operation_type",
            title="Некорректный запрос",
            status_code=422,
        )
    rule = db.scalar(select(WeatherRule).where(WeatherRule.operation_type == operation_type))
    if rule is None:
        raise AppError(
            f"Правило погодного окна для «{operation_type}» не настроено "
            f"(выполните seed-weather-rules)",
            code="rule_not_found",
            title="Не настроено",
            status_code=422,
        )

    forecasts = _forecast_days(db, field.id, days=7)
    # осадки предыдущих суток: факт за вчера, иначе день перед горизонтом из прогноза
    yesterday_fact = db.scalar(
        select(WeatherObservation).where(
            WeatherObservation.field_id == field.id,
            WeatherObservation.observed_at == date.today() - timedelta(days=1),
        )
    )
    precip_by_day: dict[date, float | None] = {
        f.forecast_for: f.precipitation_mm for f in forecasts
    }
    if yesterday_fact is not None and yesterday_fact.precipitation_mm is not None:
        precip_by_day[yesterday_fact.observed_at] = float(yesterday_fact.precipitation_mm)

    metrics = [
        DayMetrics(
            day=f.forecast_for,
            temperature_max_c=None if f.temperature_max_c is None else float(f.temperature_max_c),
            temperature_min_c=None if f.temperature_min_c is None else float(f.temperature_min_c),
            precipitation_mm=None if f.precipitation_mm is None else float(f.precipitation_mm),
            wind_max_ms=None if f.wind_max_ms is None else float(f.wind_max_ms),
            humidity_avg_pct=None if f.humidity_avg_pct is None else float(f.humidity_avg_pct),
        )
        for f in forecasts
    ]
    first_day = metrics[0].day if metrics else date.today()
    prev_precip = precip_by_day.get(first_day - timedelta(days=1))

    window_days = evaluate_window(dict(rule.params), metrics, prev_precip)
    window_open = any(d.ok for d in window_days)
    return WindowResponse(
        operation_type=operation_type,
        field_id=field.id,
        rule_text=rule.rule_text,
        window_open=window_open,
        days=[WindowDayItem(day=d.day, ok=d.ok, reasons=d.reasons) for d in window_days],
        alert=None
        if window_open or not window_days
        else (
            f"В горизонте 7 суток нет погодного окна для «{operation_type}» "
            f"({'; '.join(window_days[0].reasons)} — ближайшие сутки)"
        ),
    )
