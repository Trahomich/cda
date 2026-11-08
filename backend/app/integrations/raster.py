"""Вычисление спектральных индексов по растрам Sentinel-2 и контуру поля.

Docs/05 §2 (IF-SATELLITE), docs/06 §7.7 (#128): индексы ndvi/ndwi/ndre/
ndmi/ndsi/evi/savi/bsi, разрешение 10 м (полосы 20 м ресемплятся),
статистика (min/mean/max) — по пикселям внутри контура ENT-FIELD.
Формулы (§7.7):
- ndvi = (B08 − B04) / (B08 + B04)                NIR / RED;
- ndwi = (B03 − B08) / (B03 + B08)                вода (McFeeters);
- ndre = (B08 − B05) / (B08 + B05)                NIR / red edge;
- ndmi = (B08 − B11) / (B08 + B11)                NIR / SWIR;
- ndsi = (B03 − B11) / (B03 + B11)                снег;
- evi  = 2.5·(B08−B04)/(B08+6·B04−7.5·B02+1)      с поправкой на почву/атмосферу;
- savi = 1.5·(NIR − RED)/(NIR + RED + 0.5)         с поправкой на почву (L=0.5);
- bsi  = ((B11+B04)−(B08+B02))/((B11+B04)+(B08+B02)) доля голой почвы.

Валидация 05 §5: значение индекса вне диапазона INDEX_BOUNDS (по умолчанию
[−1; 1], EVI шире) → DataQualityError. Обработка растров идёт вне БД,
в БД — только выжимки (ADR-02).
"""

from typing import TYPE_CHECKING

import numpy as np

from app.integrations import DataQualityError

if TYPE_CHECKING:  # только для аннотаций — не тянем rasterio в импорт модуля
    from rasterio.transform import Affine

# (NIR-канал, второй канал) для каждого индекса (05 §2; §7.7 #128)
INDEX_BANDS: dict[str, tuple[str, str]] = {
    "ndvi": ("B08", "B04"),
    "ndwi": ("B03", "B08"),
    "ndre": ("B08", "B05"),
    "ndmi": ("B08", "B11"),
    "ndsi": ("B03", "B11"),
    "savi": ("B08", "B04"),
}

#: EVI и BSI — не нормализованная разность: свои формулы (§7.7),
#: отдельные функции compute_evi_array / compute_bsi_array
FORMULA_INDICES: tuple[str, ...] = ("evi", "bsi")

INDICES: tuple[str, ...] = tuple(INDEX_BANDS) + FORMULA_INDICES

#: диапазон валидации (05 §5 п.4; EVI физически до ~2.5 — §7.7 #128)
INDEX_BOUNDS: dict[str, tuple[float, float]] = {
    "evi": (-1.0, 3.0),
}

SAVI_L = 0.5  # поправка на яркость почвы (Huete, 1988)

EVI_G, EVI_C1, EVI_C2 = 2.5, 6.0, 7.5  # коэффициенты EVI (§7.7)


def compute_index_array(index: str, nir: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Массив индекса по каналам; пиксели без данных → NaN.

    Поддерживаются нормализованные разности (INDEX_BANDS, в т.ч. ndwi/ndsi)
    и SAVI. EVI/BSI — compute_evi_array / compute_bsi_array (§7.7).

    NaN: хотя бы один канал NaN, либо сумма каналов = 0 (нет отражения —
    вне плашки снимка; после маскирования контуром такие пиксели отбрасываются).
    """
    if index not in INDEX_BANDS:
        raise ValueError(f"Неизвестный индекс {index!r}; допустимо: {', '.join(INDICES)}")
    with np.errstate(divide="ignore", invalid="ignore"):
        if index == "savi":
            denom = nir + other + SAVI_L
            array = (1.0 + SAVI_L) * (nir - other) / np.where(denom == 0, np.nan, denom)
        else:
            denom = nir + other
            array = (nir - other) / np.where(denom == 0, np.nan, denom)
    return np.where(np.isfinite(array), array, np.nan)


def compute_evi_array(nir: np.ndarray, red: np.ndarray, blue: np.ndarray) -> np.ndarray:
    """EVI = 2.5·(B08−B04)/(B08+6·B04−7.5·B02+1), §7.7 #128; нет данных → NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = nir + EVI_C1 * red - EVI_C2 * blue + 1.0
        array = EVI_G * (nir - red) / np.where(denom == 0, np.nan, denom)
    return np.where(np.isfinite(array), array, np.nan)


def compute_bsi_array(nir: np.ndarray, red: np.ndarray, blue: np.ndarray, swir: np.ndarray):
    """BSI = ((B11+B04)−(B08+B02))/((B11+B04)+(B08+B02)), §7.7; нет данных → NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        soil = swir + red
        veg = nir + blue
        denom = soil + veg
        array = (soil - veg) / np.where(denom == 0, np.nan, denom)
    return np.where(np.isfinite(array), array, np.nan)


def compute_ndwi_array(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """NDWI (McFeeters) = (B03 − B08)/(B03 + B08); пиксели без данных → NaN.

    Не входит в INDICES (API-06 не отдаёт ndwi таймлайном): NDWI — вспомогательный
    канал детектора вымоканий (docs/06 §7.4), его статистика хранится колонками
    ndwi_mean/ndwi_p90 при сцене ndvi, а не отдельными сценами dat_satellite_scene.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = green + nir
        array = (green - nir) / np.where(denom == 0, np.nan, denom)
    return np.where(np.isfinite(array), array, np.nan)


def validate_index_values(index: str, values: np.ndarray) -> None:
    """Диапазон по INDEX_BOUNDS (по умолчанию [−1; 1]) — иначе DataQualityError.

    EVI — исключение (§7.7): физический диапазон шире, до ~2.5.
    """
    if values.size == 0:
        return
    lo_bound, hi_bound = INDEX_BOUNDS.get(index, (-1.0, 1.0))
    lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
    if lo < lo_bound or hi > hi_bound:
        raise DataQualityError(
            f"Индекс {index} вне [{lo_bound}; {hi_bound}]: min={lo:.3f}, max={hi:.3f} (05 §5)"
        )


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
    "FORMULA_INDICES",
    "INDEX_BANDS",
    "INDEX_BOUNDS",
    "INDICES",
    "SAVI_L",
    "compute_bsi_array",
    "compute_evi_array",
    "compute_index_array",
    "compute_ndwi_array",
    "contour_mask",
    "stats_over_mask",
    "validate_index_values",
]
