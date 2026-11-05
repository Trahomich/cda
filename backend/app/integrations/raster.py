"""Вычисление вегетационных индексов по растрам Sentinel-2 и контуру поля.

Docs/05 §2 (IF-SATELLITE): индексы ndvi/ndre/ndmi/savi, разрешение 10 м,
статистика (min/mean/max) — по пикселям внутри контура ENT-FIELD.
Каналы Sentinel-2 L2A (10 м, при необходимости ресемплинг из 20 м):
- ndvi = (B08 − B04) / (B08 + B04)                NIR / RED;
- ndre = (B08 − B05) / (B08 + B05)                NIR / red edge;
- ndmi = (B8A − B11) / (B8A + B11)                NIR / SWIR;
- savi = 1.5·(NIR − RED)/(NIR + RED + 0.5)         с поправкой на почву (L=0.5).

Валидация 05 §5: значение индекса вне [−1; 1] → DataQualityError.
Обработка растров идёт вне БД, в БД — только выжимки (ADR-02).
"""

from typing import TYPE_CHECKING

import numpy as np

from app.integrations import DataQualityError

if TYPE_CHECKING:  # только для аннотаций — не тянем rasterio в импорт модуля
    from rasterio.transform import Affine

# (NIR-канал, второй канал) для каждого индекса (05 §2)
INDEX_BANDS: dict[str, tuple[str, str]] = {
    "ndvi": ("B08", "B04"),
    "ndre": ("B08", "B05"),
    "ndmi": ("B8A", "B11"),
    "savi": ("B08", "B04"),
}

INDICES: tuple[str, ...] = tuple(INDEX_BANDS)

SAVI_L = 0.5  # поправка на яркость почвы (Huete, 1988)


def compute_index_array(index: str, nir: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Массив индекса по каналам; пиксели без данных → NaN.

    NaN: хотя бы один канал NaN, либо сумма каналов = 0 (нет отражения —
    вне плашки снимка; после маскирования контуром такие пиксели отбрасываются).
    """
    if index not in INDEX_BANDS:
        raise ValueError(f"Неизвестный индекс {index!r}; допустимо: {', '.join(INDICES)}")
    with np.errstate(divide="ignore", invalid="ignore"):
        if index == "savi":
            denom = nir + other + SAVI_L
            array = SAVI_L * (nir - other) / np.where(denom == 0, np.nan, denom)
        else:
            denom = nir + other
            array = (nir - other) / np.where(denom == 0, np.nan, denom)
    return np.where(np.isfinite(array), array, np.nan)


def validate_index_values(index: str, values: np.ndarray) -> None:
    """Диапазон [−1; 1] — иначе DataQualityError (05 §5, правило 4)."""
    if values.size == 0:
        return
    lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
    if lo < -1.0 or hi > 1.0:
        raise DataQualityError(f"Индекс {index} вне [−1; 1]: min={lo:.3f}, max={hi:.3f} (05 §5)")


def stats_over_mask(values: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
    """(min, mean, max) по пикселям маски; NaN отбрасываются.

    mask — булев массив той же формы, True = пиксель внутри контура.
    """
    selected = values[mask & np.isfinite(values)]
    if selected.size == 0:
        raise DataQualityError("Внутри контура нет валидных пикселей индекса")
    return (
        round(float(selected.min()), 3),
        round(float(selected.mean()), 3),
        round(float(selected.max()), 3),
    )


def contour_mask(
    contour_geojson: dict,
    transform: "Affine",
    out_shape: tuple[int, int],
) -> np.ndarray:
    """Булева маска пикселей внутри контура (rasterio + shapely).

    Контур — GeoJSON Polygon в CRS растра (проецируется вызывающим до вызова;
    Sentinel-2 UTM → контур WGS84 перепроецируется адаптером).
    """
    from rasterio.features import geometry_mask
    from shapely.geometry import shape

    geom = shape(contour_geojson)
    # all_touched=True: пиксель входит, если его касается контур, — на 10 м
    # разрешении и малых полях иначе теряется заметная доля площади
    return ~geometry_mask([geom], out_shape=out_shape, transform=transform, all_touched=True)


__all__ = [
    "INDICES",
    "INDEX_BANDS",
    "SAVI_L",
    "compute_index_array",
    "contour_mask",
    "stats_over_mask",
    "validate_index_values",
]
