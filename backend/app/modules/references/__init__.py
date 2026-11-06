"""Модуль справочников: чтение ref_* для фронтенда (v1-08, задача #54).

GET /references/cultures — справочник культур с color_hex: фронт красит
поля на карте Leaflet по культуре текущего цикла (#55).
"""

from app.modules.references.router import router

__all__ = ["router"]

