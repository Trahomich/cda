"""API модуля погоды: API-07, API-29, история фактов (#126), окна RULE-AGRO-09.

Эндпоинты (docs/06-data-api.md §5, §7.5):
- API-07 GET /fields/{field_id}/weather — факты (последние 14 сут) + прогноз 7 сут
  + meta источника (статус адаптера, stale-метка по issued_at > 12 ч, 05 §5);
- API-29 GET /weather/forecast?field_id&days — прогноз (кэш 3 ч на приёме);
- GET /weather/history?field_id&date_from&date_to — ежедневная история фактов,
  включая облачность/снег/часы осадков (#126, карточка погоды);
- API-58 GET /weather/gdd?field_id&year — накопительная GDD-кривая с 1 января
  года (base 5 °C, §7.8, #132);
- GET /weather/windows?operation_type&field_id — окно одной операции
  (RULE-AGRO-09); без operation_type — список окон по типам операций из
  незавершённых планов поля текущего сезона (API-59, §7.8: после уборки
  вегетационные окна не показываются);
- API-61 GET /weather/map?metric=precip_7d|precip_14d|t_now|gdd_ytd — карта
  погоды хозяйства: контуры активных полей со значением метрики (кэш 1 ч,
  §7.8, #135).

Права (07-ux §5): группа «weather» — все 5 ролей (E-06, E-10).
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.cache import cache_get, cache_set
from app.modules.satellite.waterlog import precip_sum_completed
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rbac import group_checker
from app.models.agronomy import CropCycle, Field, TreatmentPlan
from app.models.integrations import Forecast, WeatherObservation
from app.models.references import Source, WeatherRule
from app.modules.weather import hazards
from app.modules.weather import ingest as ingest_module
from app.modules.weather.rules import (
    GDD_BASE_C,
    OPERATION_TYPES,
    DayMetrics,
    evaluate_window,
)
from app.modules.weather.schemas import (
    FactItem,
    FieldWeatherResponse,
    ForecastDayItem,
    ForecastListResponse,
    GddDayItem,
    GddNormItem,
    GddResponse,
    HazardItem,
    HistoryListResponse,
    SourceMeta,
    WeatherMapItem,
    WeatherMapResponse,
    WindowDayItem,
    WindowResponse,
    WindowsListResponse,
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


def _forecast_items(
    db: Session, field_id: uuid.UUID, forecasts: list[Forecast]
) -> list[ForecastDayItem]:
    """ForecastDayItem с hazards по каждому дню (#139): культура сезона —
    один запрос, пороги — weather.hazards (docs/06 §7.8)."""
    per_day = hazards.forecast_hazards(db, field_id, forecasts)
    items = [ForecastDayItem.model_validate(f) for f in forecasts]
    for item, day_hazards in zip(items, per_day, strict=True):
        item.hazards = [HazardItem.model_validate(h) for h in day_hazards]
    return items


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
        forecast=_forecast_items(db, field.id, forecasts),
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
        items=_forecast_items(db, field.id, window),
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
        items=_fact_items_with_norm(db, field.id, date_from, date_to, window),
        total=len(rows),
        limit=limit,
        offset=offset,
        meta=_source_meta(db, forecasts=[]),
    )


def _fact_items_with_norm(
    db: Session,
    field_id: uuid.UUID,
    date_from: date,
    date_to: date,
    window: list[WeatherObservation],
) -> list[FactItem]:
    """FactItem с norm_mm — многолетняя норма осадков по календарной дате.

    Норма — среднее precipitation_mm по тем же день+месяц прошлых лет
    (полные прошлые годы, т.е. < date_from года; как norm_cum у API-58,
    §7.8). Меньше двух полных прошлых лет — norm_mm=null у всех.
    """
    items = [FactItem.model_validate(o) for o in window]
    past_years = sorted(
        {
            int(y)
            for (y,) in db.execute(
                select(func.extract("year", WeatherObservation.observed_at))
                .where(
                    WeatherObservation.field_id == field_id,
                    WeatherObservation.observed_at < date(date_from.year, 1, 1),
                )
                .distinct()
            )
        }
    )
    if len(past_years) < 2:
        return items
    by_monthday: dict[tuple[int, int], list[float]] = {}
    for obs in db.scalars(
        select(WeatherObservation).where(
            WeatherObservation.field_id == field_id,
            WeatherObservation.observed_at >= date(past_years[0], 1, 1),
            WeatherObservation.observed_at < date(date_from.year, 1, 1),
        )
    ):
        if obs.precipitation_mm is not None:
            by_monthday.setdefault((obs.observed_at.month, obs.observed_at.day), []).append(
                float(obs.precipitation_mm)
            )
    for item in items:
        day = item.observed_at
        values = by_monthday.get((day.month, day.day))
        if values:
            item.norm_mm = round(sum(values) / len(values), 1)
    return items


def _gdd_curve(
    observations: dict[date, WeatherObservation], year: int
) -> list[tuple[date, float | None, float]]:
    """GDD-кривая года: (дата, gdd_day|None, gdd_cum) по возрастанию дат.

    gdd_day = max(0, (tmax+tmin)/2 − base); день без tmin/tmax в сумму не
    входит (gdd_day=None). Кумулятив — по дням с наблюдениями (§7.8).
    """
    cum = 0.0
    curve: list[tuple[date, float | None, float]] = []
    for day in sorted(d for d in observations if d.year == year):
        obs = observations[day]
        gdd_day: float | None = None
        if obs.temperature_min_c is not None and obs.temperature_max_c is not None:
            gdd_day = round(
                max(
                    0.0,
                    (float(obs.temperature_min_c) + float(obs.temperature_max_c)) / 2 - GDD_BASE_C,
                ),
                1,
            )
            cum = round(cum + gdd_day, 1)
        curve.append((day, gdd_day, cum))
    return curve


@router.get(
    "/weather/gdd",
    response_model=GddResponse,
    summary="API-58: накопительная GDD-кривая с 1 января года (§7.8)",
)
def weather_gdd(
    db: DbSession,
    *,
    field_id: uuid.UUID = Query(description="UUID поля"),
    year: int | None = Query(
        default=None, ge=2000, le=2100, description="Год кривой; по умолчанию текущий"
    ),
) -> GddResponse:
    """Дни с наблюдениями года по возрастанию + многолетняя норма (§7.8, #132).

    Норма — среднее gdd_cum прошлых лет по календарной дате (день+месяц);
    null при <2 годах истории.
    """
    field = _get_field(db, field_id)
    chosen_year = year if year is not None else date.today().year

    # одна запись на дату (источник у поля один; при дубле — большая trust)
    by_date: dict[date, WeatherObservation] = {}
    for obs in db.scalars(
        select(WeatherObservation)
        .where(WeatherObservation.field_id == field.id)
        .order_by(WeatherObservation.observed_at)
    ):
        prev = by_date.get(obs.observed_at)
        if prev is None or float(obs.trust) > float(prev.trust):
            by_date[obs.observed_at] = obs

    curve = _gdd_curve(by_date, chosen_year)
    past_years = sorted({d.year for d in by_date if d.year < chosen_year})
    norm_items: list[GddNormItem] | None = None
    if len(past_years) >= 2:
        # кумулятив каждого прошлого года, ключ — календарная дата (день+месяц)
        past_curves = [
            {(d.month, d.day): cum for d, _gdd, cum in _gdd_curve(by_date, y)} for y in past_years
        ]
        norm_items = []
        for day, _gdd, _cum in curve:
            values = [pc[(day.month, day.day)] for pc in past_curves if (day.month, day.day) in pc]
            norm_items.append(
                GddNormItem(
                    date=day,
                    norm_cum=round(sum(values) / len(values), 1) if values else None,
                )
            )

    return GddResponse(
        field_id=field.id,
        year=chosen_year,
        base_c=GDD_BASE_C,
        total=curve[-1][2] if curve else 0.0,
        items=[GddDayItem(date=d, gdd_day=gdd, gdd_cum=cum) for d, gdd, cum in curve],
        norm_cum=norm_items,
    )


def _planned_treatment_types(db: Session, field_id: uuid.UUID, today: date) -> set[str]:
    """Типы операций из незавершённых планов внесения текущего сезона (§7.8).

    Сезон — main-цикл по году уборки (как current_culture_name: месяцы 9–12
    сначала смотрят год+1 — озимые под будущий сезон). Цикл, закрытый
    уборкой (status='completed'), вегетационных операций не даёт: планы
    внесения после уборки не показываем (фидбек #391 п.3). Незавершённые
    планы — draft/approved/in_progress (в модели нет «planned», это
    отображение контракта «НЕзавершённые», 03 §2.1).
    """
    years = (today.year + 1, today.year) if today.month >= 9 else (today.year, today.year - 1)
    cycle = db.scalar(
        select(CropCycle).where(
            CropCycle.field_id == field_id,
            CropCycle.kind == "main",
            CropCycle.year.in_(years),
        )
    )
    if cycle is None or cycle.status == "completed":
        return set()
    return set(
        db.scalars(
            select(TreatmentPlan.operation_type).where(
                TreatmentPlan.cycle_id == cycle.id,
                TreatmentPlan.status.in_(("draft", "approved", "in_progress")),
            )
        )
    )


def _forecast_metrics(
    db: Session, field_id: uuid.UUID
) -> tuple[list[DayMetrics], dict[date, float | None]]:
    """Метрики горизонта 7 сут + осадки предыдущих суток по факту (API-59)."""
    forecasts = _forecast_days(db, field_id, days=7)
    # осадки предыдущих суток: факт за вчера, иначе день перед горизонтом из прогноза
    yesterday_fact = db.scalar(
        select(WeatherObservation).where(
            WeatherObservation.field_id == field_id,
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
    return metrics, precip_by_day


def _window_response(
    field: Field,
    rule: WeatherRule,
    metrics: list[DayMetrics],
    precip_by_day: dict[date, float | None],
) -> WindowResponse:
    """Окно одного типа по общим метрикам горизонта (API-59)."""
    first_day = metrics[0].day if metrics else date.today()
    prev_precip = precip_by_day.get(first_day - timedelta(days=1))
    window_days = evaluate_window(dict(rule.params), metrics, prev_precip)
    window_open = any(d.ok for d in window_days)
    nearest = next((d.day for d in window_days if d.ok), None)
    return WindowResponse(
        operation_type=rule.operation_type,
        field_id=field.id,
        rule_text=rule.rule_text,
        window_open=window_open,
        nearest_window_date=nearest,
        days=[WindowDayItem(day=d.day, ok=d.ok, reasons=d.reasons) for d in window_days],
        alert=None
        if window_open or not window_days
        else (
            f"В горизонте 7 суток нет погодного окна для «{rule.operation_type}» "
            f"({'; '.join(window_days[0].reasons)} — ближайшие сутки)"
        ),
    )


@router.get(
    "/weather/windows",
    response_model=WindowResponse | WindowsListResponse,
    summary="API-59: погодное окно операции (RULE-AGRO-09) / список окон по планам поля",
)
def weather_window(
    db: DbSession,
    *,
    operation_type: str | None = Query(
        default=None,
        description=(
            "Одиночный режим: тип операции "
            f"({', '.join(OPERATION_TYPES)}). Без параметра — список окон "
            "по типам операций активных планов поля (§7.8)"
        ),
    ),
    field_id: uuid.UUID = Query(description="UUID поля"),
    only_planned: bool = Query(
        default=True,
        description="Список-режим: только типы из незавершённых планов текущего сезона",
    ),
) -> WindowResponse | WindowsListResponse:
    """Два режима (§7.8 v1.8): одиночное окно или список по планам поля."""
    field = _get_field(db, field_id)

    if operation_type is not None:
        if operation_type not in OPERATION_TYPES:
            raise AppError(
                f"Неизвестный тип операции «{operation_type}»; допустимо: "
                f"{', '.join(OPERATION_TYPES)}",
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
        metrics, precip_by_day = _forecast_metrics(db, field.id)
        return _window_response(field, rule, metrics, precip_by_day)

    # список-режим: окна по типам операций поля (§7.8)
    rules_rows = list(db.scalars(select(WeatherRule).order_by(WeatherRule.operation_type)))
    if only_planned:
        planned = _planned_treatment_types(db, field.id, date.today())
        rules_rows = [r for r in rules_rows if r.operation_type in planned]
    metrics, precip_by_day = _forecast_metrics(db, field.id)
    items = [_window_response(field, rule, metrics, precip_by_day) for rule in rules_rows]
    hint = "нет активных планов операций" if only_planned and not items else None
    return WindowsListResponse(
        field_id=field.id,
        only_planned=only_planned,
        items=items,
        hint=hint,
    )


#: метрики карты погоды (API-61, §7.8): окно осадков / последняя суточная
#: температура / накопленная GDD с начала года (base 5 °C, как API-58)
WEATHER_MAP_METRICS = ("precip_7d", "precip_14d", "t_now", "gdd_ytd")
WEATHER_MAP_CACHE_TTL = 3600  # 1 ч (§7.8)
WEATHER_MAP_SIMPLIFY_TOLERANCE = 0.0005  # ~50 м, градусы: контуры для заливки


def _weather_map_cache_key(metric: str) -> str:
    return f"weather:map:{metric}"


@router.get(
    "/weather/map",
    response_model=WeatherMapResponse,
    summary="API-61: карта погоды хозяйства — заливка полей по метрике (§7.8)",
)
def weather_map(
    db: DbSession,
    *,
    metric: str = Query(
        default="precip_7d",
        description="precip_7d | precip_14d (сумма осадков за окно), t_now "
        "(последняя суточная температура), gdd_ytd (GDD с 1 января, base 5 °C)",
    ),
) -> WeatherMapResponse:
    """Контуры всех активных полей со значением метрики (§7.8, #135).

    Кэш 1 ч (ключ weather:map:{metric}); value=null — наблюдений нет.
    """
    if metric not in WEATHER_MAP_METRICS:
        raise AppError(
            f"Неизвестная метрика «{metric}»; допустимо: {', '.join(WEATHER_MAP_METRICS)}",
            code="unknown_metric",
            title="Некорректный запрос",
            status_code=422,
        )
    cache_key = _weather_map_cache_key(metric)
    if (cached := cache_get(cache_key)) is not None:
        return WeatherMapResponse.model_validate(cached)

    today = date.today()
    fields_rows = db.execute(
        text(
            "SELECT f.id, f.name, ST_AsGeoJSON(ST_Simplify(f.contour::geometry, "
            ":tol)) FROM dat_field f WHERE f.status = 'active' "
            "AND f.contour IS NOT NULL ORDER BY f.name"
        ),
        {"tol": WEATHER_MAP_SIMPLIFY_TOLERANCE},
    ).fetchall()
    field_ids = [row[0] for row in fields_rows]

    # наблюдения по всем полям за нужный период: одна запись на дату (большая
    # trust), как API-58; для осадков — окно метрики, для t_now/gdd — весь год
    if metric in ("precip_7d", "precip_14d"):
        window = 7 if metric == "precip_7d" else 14
        # #204: завершённые сутки [T−N, T−1] — единое окно со сводкой API-07
        date_from = today - timedelta(days=window)
    else:
        window = 0
        date_from = date(today.year, 1, 1)
    date_to = today if window == 0 else today - timedelta(days=1)
    per_field: dict[uuid.UUID, dict[date, WeatherObservation]] = {}
    if field_ids:
        for obs in db.scalars(
            select(WeatherObservation)
            .where(
                WeatherObservation.field_id.in_(field_ids),
                WeatherObservation.observed_at >= date_from,
                WeatherObservation.observed_at <= date_to,
            )
            .order_by(WeatherObservation.observed_at)
        ):
            bucket = per_field.setdefault(obs.field_id, {})
            prev = bucket.get(obs.observed_at)
            if prev is None or float(obs.trust) > float(prev.trust):
                bucket[obs.observed_at] = obs

    items: list[WeatherMapItem] = []
    for field_id, name, contour_raw in fields_rows:
        observations = per_field.get(field_id, {})
        value: float | None = None
        updated_at: date | None = max(observations) if observations else None
        if not observations:
            value = None  # наблюдений нет — метрика не определена (§7.8)
        elif metric in ("precip_7d", "precip_14d"):
            # #204: единая функция со сводкой API-07 и детекторами
            value = precip_sum_completed(db, field_id, today, days=window)
        else:
            latest = observations[max(observations)]
            if metric == "t_now":
                if latest.temperature_avg_c is not None:
                    value = float(latest.temperature_avg_c)
                elif latest.temperature_min_c is not None and latest.temperature_max_c is not None:
                    value = round(
                        (float(latest.temperature_min_c) + float(latest.temperature_max_c)) / 2,
                        1,
                    )
            else:  # gdd_ytd — как API-58: дни с обеими tmin/tmax, base 5 °C
                value = round(
                    sum(
                        max(
                            0.0,
                            (float(o.temperature_min_c) + float(o.temperature_max_c)) / 2
                            - GDD_BASE_C,
                        )
                        for o in observations.values()
                        if o.temperature_min_c is not None and o.temperature_max_c is not None
                    ),
                    1,
                )
        items.append(
            WeatherMapItem(
                field_id=field_id,
                name=name,
                contour=json.loads(contour_raw),
                value=value,
                updated_at=updated_at,
            )
        )

    resp = WeatherMapResponse(metric=metric, items=items)
    cache_set(cache_key, resp.model_dump(mode="json"), WEATHER_MAP_CACHE_TTL)
    return resp
