"""Модуль спутника (MVP-10): конвейер Sentinel-2 → NDVI, API-06 (ENT-SATELLITE-SCENE).

Конвейер: каталог Copernicus → сцены по контуру → статистика индекса →
dat_satellite_scene; событие scene.ingested в шину (ADR-01); опрос раз в 6 ч
(05 §4) за флагом satellite_enabled.
"""

from app.modules.satellite.router import router

__all__ = ["router"]
