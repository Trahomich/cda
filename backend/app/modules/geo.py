"""Общие гео-хелперы модулей ЦДА (v1-02)."""

import json
from typing import Any


def unwrap_single_multi(geo: Any) -> Any:
    """MultiPolygon из одного полигона → Polygon (совместимость API).

    Колонки contour — geography(MULTIPOLYGON,4326) (составные контуры
    реальных хозяйств), но контракты API-02/06/14 отдают одиночный контур
    как Polygon: иначе ломаются фронт и старые клиенты.
    """
    if (
        isinstance(geo, dict)
        and geo.get("type") == "MultiPolygon"
        and isinstance(geo.get("coordinates"), list)
        and len(geo["coordinates"]) == 1
    ):
        return {"type": "Polygon", "coordinates": geo["coordinates"][0]}
    return geo


def parse_geojson(raw: str | None) -> Any:
    """Строка ST_AsGeoJSON → объект GeoJSON (одиночный мульти развёрнут)."""
    return unwrap_single_multi(json.loads(raw or "null"))
