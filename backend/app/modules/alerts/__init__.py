"""Модуль алертов: генераторы на нормализованных данных, API-27/28 (MVP-12).

Правила-генераторы (ADR-04, простые правила MVP):
- погода: неблагоприятное явление в прогнозе (dat_forecast.hazard: frost/hail/
  heavy_rain/wind) на поле с активным циклом → ENT-ALERT type=weather_danger
  (frost/hail — critical, прочие — warning);
- NDVI: падение mean NDVI между соседними сценами более чем на alerts_ndvi_drop_pct
  за окно alerts_ndvi_window_days → type=ndvi_anomaly, warning (REQ-F-029 базово);
- спутниковая пауза: нет свежих сцен alerts_satellite_stale_days суток →
  type=data_source, info.

Дедупликация: не создаётся активный (status != closed) алерт того же
type + field_id. data_refs — в payload JSONB (docs/03 §1.8: у ENT-ALERT
детали события в payload; data_refs отдельным полем — у ENT-RECOMMENDATION).
"""

from app.modules.alerts.router import router

__all__ = ["router"]
