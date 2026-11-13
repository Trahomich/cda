"""Адаптер IF-WEATHER «Open-Meteo» (docs/05-integrations.md §2, REC-16).

Общедоступный REST без ключа: текущая погода и прогноз — api.open-meteo.com,
архив — archive-api.open-meteo.com. Адаптер только нормализует ответы
в структуры из ``app.integrations``; в БД пишет ingest-слой модуля weather.

Ветер запрашивается в м/с (``windspeed_unit=ms``), осадки — в мм, влажность — в %.

Маппинги (Open-Meteo отдаёт только max/min, а не среднесуточную):
- temperature_avg_c = (t_max + t_min) / 2 — стандартная суточная оценка;
- hazard (05 §3): frost — t_min ≤ 0 °C; heavy_rain — осадки ≥ 15 мм;
  wind — ветер ≥ 15 м/с; hail открытых данных нет — не выставляется.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.integrations import (
    DataGapError,
    DataQualityError,
    ForecastItem,
    Observation,
    SourceCapabilities,
    SourceUnavailableError,
)

logger = logging.getLogger(__name__)

# Валидация диапазонов (05 §5)
T_MIN_LIMIT_C = -60.0
T_MAX_LIMIT_C = 60.0
WIND_LIMIT_MS = 60.0

# Пороги опасных явлений (ENT-FORECAST.hazard)
HAZARD_FROST_T = 0.0
HAZARD_HEAVY_RAIN_MM = 15.0
HAZARD_WIND_MS = 15.0

# Метрики суточные, общие для forecast- и archive-API (05 §2, #126, #193).
_DAILY_METRICS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "relative_humidity_2m_mean",
    "cloud_cover_mean",
    "snowfall_sum",
    "snow_depth_max",
    "precipitation_hours",
    # #193: ET0 (мм/сут), УФ (максимум), точка росы (средняя), давление
    # на уровне моря (среднее, гПа) — daily есть в обоих API
    "et0_fao_evapotranspiration",
    "uv_index_max",
    "dew_point_2m_mean",
    "pressure_msl_mean",
)

# Почва — ТОЛЬКО hourly (daily-агрегатов нет): прогноз 6 см, архив ERA5 0–7 см;
# агрегируем в суточные средние сами (#193). Влажность — м³/м³ → % ×100.
_HOURLY_SOIL_FORECAST = ("soil_temperature_6cm", "soil_moisture_1_to_3cm")
_HOURLY_SOIL_ARCHIVE = ("soil_temperature_0_to_7cm", "soil_moisture_1_to_3cm")

# Вероятность осадков отдаёт только forecast-API (в архиве её нет).
_FORECAST_ONLY_METRICS = ("precipitation_probability_max",)

CentroidLookup = Callable[[str], tuple[float, float] | None]
"""field_id (UUID строкой) -> (longitude, latitude) или None, если центр не найден."""


def _iso_now() -> datetime:
    return datetime.now(tz=UTC)


class OpenMeteoAdapter:
    """HTTP-адаптер Open-Meteo (source_id="weather.open-meteo", 05 §2)."""

    source_id = "weather.open-meteo"
    capabilities = SourceCapabilities(
        source_id=source_id,
        kind="weather",
        history=True,
        current=True,
        forecast_days=16,
        metrics=("temperature", "precipitation", "wind", "humidity", "cloud", "snow"),
    )

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        centroid_lookup: CentroidLookup,
        base_url: str = "https://api.open-meteo.com/v1",
        archive_url: str = "https://archive-api.open-meteo.com/v1",
        request_timeout: float = 10.0,
    ) -> None:
        self._centroid_lookup = centroid_lookup
        self._base_url = base_url.rstrip("/")
        self._archive_url = archive_url.rstrip("/")
        self._timeout = request_timeout
        self._client = http  # None -> лениво создаётся свой

    # -- внутреннее ---------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _centroid(self, field_id: str) -> tuple[float, float]:
        point = self._centroid_lookup(field_id)
        if point is None:
            raise DataGapError(f"Нет контура/центроида у поля {field_id}")
        lon, lat = point
        if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
            raise DataQualityError(f"Центроид поля {field_id} вне диапазона: lon={lon} lat={lat}")
        return lon, lat

    def _get(self, base_url: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET с переводом сетевых/5xx ошибок в SourceUnavailableError (05 §6).

        400 с почвенными hourly-метриками (провайдер изменил имена) — повтор
        без них: деградация без почвы лучше недоступности источника (#193).
        """
        try:
            response = self._http().get(base_url, params=params)
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"{self.source_id}: сеть недоступна: {exc}") from exc
        if response.status_code == 400 and "hourly" in params:
            logger.warning(
                "%s: HTTP 400 с почвенными hourly (%s) — повтор без почвы",
                self.source_id,
                response.text[:120],
            )
            params = {k: v for k, v in params.items() if k != "hourly"}
            return self._get(base_url, params)
        if response.status_code >= 500:
            raise SourceUnavailableError(
                f"{self.source_id}: HTTP {response.status_code} от {base_url}"
            )
        if response.status_code == 400:
            # 400 у Open-Meteo — как правило, параметры; это недоступность сервиса
            raise SourceUnavailableError(f"{self.source_id}: HTTP 400 ({response.text[:200]})")
        if response.status_code != 200:
            raise DataGapError(
                f"{self.source_id}: нет данных (HTTP {response.status_code} от {base_url})"
            )
        return response.json()

    @staticmethod
    def _hourly_soil_means(
        payload: dict[str, Any],
    ) -> dict[date, tuple[float | None, float | None]]:
        """Суточные средние почвы из hourly-ответа: (T °C, влажность %).

        Влажность провайдера — м³/м³ (доли) → % объёмной влажности ×100.
        Нет hourly (деградация/нет данных) — пустой словарь.
        """
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        keys = [k for k in hourly if k != "time"]
        if not times or not keys:
            return {}
        acc: dict[date, list[list[float | None]]] = {}
        for i, ts in enumerate(times):
            day = date.fromisoformat(str(ts)[:10])
            bucket = acc.setdefault(day, [[] for _ in keys])
            for j, key in enumerate(keys):
                series = hourly.get(key) or []
                if i < len(series) and series[i] is not None:
                    bucket[j].append(float(series[i]))
        out: dict[date, tuple[float | None, float | None]] = {}
        for day, bucket in acc.items():
            means = [
                round(sum(v for v in values if v is not None) / len(values), 2) if values else None
                for values in bucket
            ]
            temp = means[0] if len(means) > 0 else None
            moist = means[1] if len(means) > 1 else None
            out[day] = (
                temp,
                None if moist is None else round(moist * 100.0, 1),
            )
        return out

    @staticmethod
    def _daily_rows(payload: dict[str, Any]) -> list[tuple[date, dict[str, float | None]]]:
        """Разложить ответ daily в список (дата, метрики)."""
        daily = payload.get("daily") or {}
        times = daily.get("time") or []
        if not times:
            raise DataGapError("Источник не вернул ни одних суток (daily.time пуст)")
        rows: list[tuple[date, dict[str, float | None]]] = []
        for i, day_str in enumerate(times):

            def value(metric: str, _i: int = i) -> float | None:
                series = daily.get(metric) or []
                if _i >= len(series) or series[_i] is None:
                    return None
                return float(series[_i])

            rows.append(
                (
                    date.fromisoformat(str(day_str)),
                    {m: value(m) for m in _DAILY_METRICS + _FORECAST_ONLY_METRICS},
                )
            )
        return rows

    @staticmethod
    def _validate(day: date, metrics: dict[str, float | None]) -> None:
        """Диапазоны 05 §5: T вне [-60; +60] °C, ветер > 60 м/с -> DataQualityError."""
        t_max = metrics.get("temperature_2m_max")
        t_min = metrics.get("temperature_2m_min")
        for name, t in (("t_max", t_max), ("t_min", t_min)):
            if t is not None and not T_MIN_LIMIT_C <= t <= T_MAX_LIMIT_C:
                raise DataQualityError(f"{day}: {name}={t} вне [-60; +60] °C")
        wind = metrics.get("wind_speed_10m_max")
        if wind is not None and wind > WIND_LIMIT_MS:
            raise DataQualityError(f"{day}: ветер {wind} м/с > 60 м/с")
        precip = metrics.get("precipitation_sum")
        if precip is not None and precip < 0:
            raise DataQualityError(f"{day}: отрицательные осадки {precip} мм")
        humidity = metrics.get("relative_humidity_2m_mean")
        if humidity is not None and not 0 <= humidity <= 100:
            raise DataQualityError(f"{day}: влажность {humidity} вне 0–100 %")
        prob = metrics.get("precipitation_probability_max")
        if prob is not None and not 0 <= prob <= 100:
            raise DataQualityError(f"{day}: вероятность осадков {prob} вне 0–100 %")
        cloud = metrics.get("cloud_cover_mean")
        if cloud is not None and not 0 <= cloud <= 100:
            raise DataQualityError(f"{day}: облачность {cloud} вне 0–100 %")
        for name, value in (
            ("снегопад", metrics.get("snowfall_sum")),
            ("высота снега", metrics.get("snow_depth_max")),
        ):
            if value is not None and value < 0:
                raise DataQualityError(f"{day}: {name} {value} см < 0")
        hours = metrics.get("precipitation_hours")
        if hours is not None and not 0 <= hours <= 24:
            raise DataQualityError(f"{day}: часы осадков {hours} вне 0–24")
        # #193: почва/ET0/УФ/роса/давление (05 §5)
        soil_t = metrics.get("soil_temperature_mean")
        if soil_t is not None and not -40.0 <= soil_t <= 50.0:
            raise DataQualityError(f"{day}: температура почвы {soil_t} вне [-40; +50] °C")
        soil_m = metrics.get("soil_moisture_pct_mean")
        if soil_m is not None and not 0.0 <= soil_m <= 100.0:
            raise DataQualityError(f"{day}: влажность почвы {soil_m} вне 0–100 %")
        et0 = metrics.get("et0_fao_evapotranspiration")
        if et0 is not None and et0 < 0:
            raise DataQualityError(f"{day}: ET0 {et0} < 0 мм")
        uv = metrics.get("uv_index_max")
        if uv is not None and not 0.0 <= uv <= 20.0:
            raise DataQualityError(f"{day}: УФ-индекс {uv} вне 0–20")
        pressure = metrics.get("pressure_msl_mean")
        if pressure is not None and not 800.0 <= pressure <= 1150.0:
            raise DataQualityError(f"{day}: давление {pressure} вне 800–1150 гПа")
        dew = metrics.get("dew_point_2m_mean")
        if dew is not None and t_max is not None and dew > t_max + 0.5:
            # точка росы физически не выше температуры воздуха; +0.5 °C —
            # допуск округления суточных агрегатов
            raise DataQualityError(f"{day}: точка росы {dew} выше t_max {t_max} (допуск 0.5 °C)")

    # -- публичные методы контракта IF-WEATHER (05 §2) -----------------------

    def fetch_forecast(self, field_id: str, days: int) -> list[ForecastItem]:
        """Прогноз на days суток (≤ 16, 05 §2); issued_at — момент выдачи."""
        if not 1 <= days <= self.capabilities.forecast_days:
            raise ValueError(f"days должен быть 1..{self.capabilities.forecast_days}")
        lon, lat = self._centroid(field_id)
        payload = self._get(
            f"{self._base_url}/forecast",
            {
                "latitude": lat,
                "longitude": lon,
                "daily": ",".join(_DAILY_METRICS + _FORECAST_ONLY_METRICS),
                "hourly": ",".join(_HOURLY_SOIL_FORECAST),
                "timezone": "auto",
                "windspeed_unit": "ms",
                "forecast_days": days,
            },
        )
        soil = self._hourly_soil_means(payload)
        issued_at = _iso_now()
        items: list[ForecastItem] = []
        for day, metrics in self._daily_rows(payload):
            metrics["soil_temperature_mean"], metrics["soil_moisture_pct_mean"] = soil.get(
                day, (None, None)
            )
            self._validate(day, metrics)
            items.append(self._to_forecast(field_id, day, issued_at, metrics))
        return items

    def fetch_current(self, field_id: str) -> Observation:
        """Текущая погода как суточный факт за сегодня (est): t_avg = (max+min)/2."""
        lon, lat = self._centroid(field_id)
        payload = self._get(
            f"{self._base_url}/forecast",
            {
                "latitude": lat,
                "longitude": lon,
                "daily": ",".join(_DAILY_METRICS + _FORECAST_ONLY_METRICS),
                "hourly": ",".join(_HOURLY_SOIL_FORECAST),
                "timezone": "auto",
                "windspeed_unit": "ms",
                "forecast_days": 1,
            },
        )
        soil = self._hourly_soil_means(payload)
        # только сегодняшние сутки
        rows = [row for row in self._daily_rows(payload) if row[0] == date.today()]
        if not rows:
            rows = self._daily_rows(payload)[:1]
        day, metrics = rows[0]
        metrics["soil_temperature_mean"], metrics["soil_moisture_pct_mean"] = soil.get(
            day, (None, None)
        )
        self._validate(day, metrics)
        return self._to_observation(field_id, day, metrics)

    def fetch_history(self, field_id: str, date_from: date, date_to: date) -> list[Observation]:
        """Архив фактов за период (archive-api, 05 §2)."""
        if date_from > date_to:
            raise ValueError("date_from позже date_to")
        lon, lat = self._centroid(field_id)
        payload = self._get(
            f"{self._archive_url}/archive",
            {
                "latitude": lat,
                "longitude": lon,
                "daily": ",".join(_DAILY_METRICS),
                "hourly": ",".join(_HOURLY_SOIL_ARCHIVE),
                "timezone": "auto",
                "windspeed_unit": "ms",
                "start_date": date_from.isoformat(),
                "end_date": date_to.isoformat(),
            },
        )
        soil = self._hourly_soil_means(payload)
        observations = []
        for day, metrics in self._daily_rows(payload):
            metrics["soil_temperature_mean"], metrics["soil_moisture_pct_mean"] = soil.get(
                day, (None, None)
            )
            self._validate(day, metrics)
            observations.append(self._to_observation(field_id, day, metrics))
        return observations

    # -- нормализация --------------------------------------------------------

    @staticmethod
    def _to_observation(field_id: str, day: date, m: dict[str, float | None]) -> Observation:
        t_max, t_min = m["temperature_2m_max"], m["temperature_2m_min"]
        t_avg = None if t_max is None or t_min is None else round((t_max + t_min) / 2, 1)
        return Observation(
            field_id=field_id,
            observed_at=day,
            temperature_avg_c=t_avg,
            temperature_min_c=t_min,
            temperature_max_c=t_max,
            precipitation_mm=m["precipitation_sum"],
            wind_max_ms=m["wind_speed_10m_max"],
            humidity_avg_pct=m["relative_humidity_2m_mean"],
            source_id=OpenMeteoAdapter.source_id,
            cloud_cover_mean_pct=m.get("cloud_cover_mean"),
            snowfall_cm=m.get("snowfall_sum"),
            snow_depth_cm=m.get("snow_depth_max"),
            precipitation_hours=m.get("precipitation_hours"),
            soil_temperature_c=m.get("soil_temperature_mean"),
            soil_moisture_pct=m.get("soil_moisture_pct_mean"),
            et0_mm=m.get("et0_fao_evapotranspiration"),
            uv_index=m.get("uv_index_max"),
            dew_point_c=m.get("dew_point_2m_mean"),
            pressure_hpa=m.get("pressure_msl_mean"),
        )

    @staticmethod
    def _to_forecast(
        field_id: str, day: date, issued_at: datetime, m: dict[str, float | None]
    ) -> ForecastItem:
        t_min = m["temperature_2m_min"]
        precip = m["precipitation_sum"]
        wind = m["wind_speed_10m_max"]
        if t_min is not None and t_min <= HAZARD_FROST_T:
            hazard = "frost"
        elif precip is not None and precip >= HAZARD_HEAVY_RAIN_MM:
            hazard = "heavy_rain"
        elif wind is not None and wind >= HAZARD_WIND_MS:
            hazard = "wind"
        else:
            hazard = "none"
        # вероятность осадков отдаёт только forecast-API (архив — None)
        prob = m.get("precipitation_probability_max")
        return ForecastItem(
            field_id=field_id,
            forecast_for=day,
            issued_at=issued_at,
            temperature_min_c=t_min,
            temperature_max_c=m["temperature_2m_max"],
            precipitation_mm=precip,
            wind_max_ms=wind,
            humidity_avg_pct=m["relative_humidity_2m_mean"],
            precip_prob_pct=None if prob is None else int(prob),
            hazard=hazard,
            source_id=OpenMeteoAdapter.source_id,
            soil_temp_c=m.get("soil_temperature_mean"),
            soil_moisture_pct=m.get("soil_moisture_pct_mean"),
            et0_mm=m.get("et0_fao_evapotranspiration"),
            uv_index=m.get("uv_index_max"),
            dew_point_c=m.get("dew_point_2m_mean"),
            pressure_hpa=m.get("pressure_msl_mean"),
        )


def default_history_window(days: int = 7) -> tuple[date, date]:
    """Окно истории для backfill: последние days суток до вчера включительно."""
    today = date.today()
    return today - timedelta(days=days), today - timedelta(days=1)


__all__ = ["CentroidLookup", "OpenMeteoAdapter", "default_history_window"]
