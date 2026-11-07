"""Рендеры сцены поля (PNG) для карточки поля — docs/06 §7.3, задача #83.

Генерируются в цикле NDVI (sentinel-ingest) вместе со статистикой сцены:

- ``ndvi`` — NDVI-карта, палитра «коричневый → жёлтый → зелёный» с
  фиксированными опорными точками (фикс. гамма); облака (SCL 8/9/10) и
  пиксели вне контура — прозрачные;
- ``truecolor`` — композит видимых каналов B04/B03/B02 с перцентильной
  нормировкой яркости; вне контура — прозрачные, облака — как есть;
- ``ndwi`` — NDWI-карта «сухо → влажно → вода» (docs/06 §7.4, #91),
  палитра жёлтый → бирюзовый → синий; облака и вне контура — прозрачные.

Хранение (§7.3): ``{render_dir}/{field_id}/{variant}/{date}.png`` плюс
сайдкар ``{render_dir}/{field_id}/meta/{date}.json`` — bbox окна растра в
WGS84 и размер в пикселях (для заголовков X-Field-Bbox/X-Image-Size).
Размер контролируется: окно по контуру, прореживание до MAX_SIDE_PX.

Ошибки рендера не роняют цикл NDVI: вызывающая сторона ловит исключения
(лог, рендера нет → render_available=false). Отсутствие каналов b02/b03
в активах каталога (строки, каталогизированные до #83) — не ошибка:
рендер просто пропускается, сцена сохраняется как раньше (в т.ч. без
NDWI-статистики ndwi_mean/ndwi_p90).
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from app.integrations.raster import compute_index_array, compute_ndwi_array, contour_mask

logger = logging.getLogger(__name__)

#: варианты рендера: каталог ndvi-renders/{field_id}/{variant}/ (§7.3 + §7.4)
RENDER_VARIANTS = ("ndvi", "truecolor", "ndwi")

#: варианты, обязательные для render_available (§7.3: «PNG обоих вариантов» —
#: ndvi и truecolor; ndwi добавлен #91 и у сцен, отрендеренных кодом #83,
#: отсутствует — это не делает render_available=false)
REQUIRED_VARIANTS = ("ndvi", "truecolor")

#: максимум по длинной стороне PNG: окно поля прореживается до него
MAX_SIDE_PX = 1024

#: опорные точки палитры NDVI (фикс. гамма, §7.3): коричневый → жёлтый → зелёный
PALETTE: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (139, 69, 19)),
    (0.35, (222, 194, 77)),
    (0.65, (132, 183, 72)),
    (1.0, (24, 122, 55)),
)

#: NDVI отображаемого диапазона: −0.1 → 0 (голая почва/вода), 0.8 → 1 (пик вегетации)
_NDVI_LO, _NDVI_HI = -0.1, 0.8

#: NDWI отображаемого диапазона (§7.4): < −0.3 сухо, > 0.5 открытая вода
_NDWI_LO, _NDWI_HI = -0.3, 0.5

#: опорные точки палитры NDWI (§7.4): сухо → влажно → вода
PALETTE_NDWI: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (224, 196, 68)),  # сухо — жёлтый
    (0.5, (72, 199, 185)),  # влажно — бирюзовый
    (1.0, (24, 84, 205)),  # вода — синий
)

#: перцентили нормировки яркости truecolor (отражения L2A, типично 0–3000)
_TC_LO_PCT, _TC_HI_PCT = 2.0, 98.0


#: индекс палитры NDVI (совпадает с ndvi.INDEX)
INDEX_NAME = "ndvi"


@dataclass(frozen=True, slots=True)
class SceneRender:
    """Готовые PNG одной сцены: оба варианта + геопривязка окна."""

    png: dict[str, bytes]  # variant → PNG-байты (RGBA)
    width: int
    height: int
    bbox: tuple[float, float, float, float]  # WGS84: minLon, minLat, maxLon, maxLat


def render_path(render_dir: Path | str, field_id, variant: str, captured_at: date) -> Path:
    """Путь PNG рендера (§7.3): {render_dir}/{field_id}/{variant}/{date}.png."""
    return Path(render_dir) / str(field_id) / variant / f"{captured_at.isoformat()}.png"


def meta_path(render_dir: Path | str, field_id, captured_at: date) -> Path:
    """Путь сайдкара геопривязки: {render_dir}/{field_id}/meta/{date}.json."""
    return Path(render_dir) / str(field_id) / "meta" / f"{captured_at.isoformat()}.json"


def renders_exist(render_dir: Path | str, field_id, captured_at: date) -> bool:
    """true, если на диске есть PNG обязательных вариантов (render_available, §7.3)."""
    return all(
        render_path(render_dir, field_id, variant, captured_at).is_file()
        for variant in REQUIRED_VARIANTS
    )


def _palette_rgb(t: np.ndarray, palette: tuple[tuple[float, tuple[int, int, int]], ...]) -> np.ndarray:
    """t ∈ [0,1] → RGB по опорным точкам палитры (линейная интерполяция)."""
    stops = np.array([s for s, _ in palette], dtype="float32")
    colors = np.array([c for _, c in palette], dtype="float32")
    rgb = np.zeros(t.shape + (3,), dtype="uint8")
    for channel in range(3):
        rgb[..., channel] = np.interp(t, stops, colors[:, channel]).astype("uint8")
    return rgb


def _png_rgba(rgb: np.ndarray, alpha: np.ndarray) -> bytes:
    """RGBA-массивы → PNG-байты (rasterio MemoryFile, без Pillow)."""
    from rasterio.io import MemoryFile

    height, width = alpha.shape
    with MemoryFile() as mem:
        with mem.open(
            driver="PNG",
            height=height,
            width=width,
            count=4,
            dtype="uint8",
        ) as dst:
            dst.write(rgb[..., 0], 1)
            dst.write(rgb[..., 1], 2)
            dst.write(rgb[..., 2], 3)
            dst.write(alpha, 4)
        return mem.read()


def render_scene(assets: dict, contour_geojson: dict) -> SceneRender | None:
    """Рендер обоих вариантов по COG-активам сцены и контуру поля.

    ``None`` — рендер невозможен без ошибки: в активах нет каналов b02/b03
    (строки каталога до #83). Сетевые ошибки COG (SourceUnavailableError)
    пробрасываются — их глотает вызывающий цикл вместе с остальными.
    """
    from rasterio.warp import transform_bounds, transform_geom

    from app.services.sentinel_ingest.ndvi import _read_band_window, _read_scl_grid

    for band in ("b02", "b03"):
        if not assets.get(band):
            return None

    red_arr, red_transform, red_crs = _read_band_window(assets["b04"], contour_geojson)
    nir_arr, nir_transform, _ = _read_band_window(assets["b08"], contour_geojson)
    green_arr, green_transform, _ = _read_band_window(assets["b03"], contour_geojson)
    blue_arr, blue_transform, _ = _read_band_window(assets["b02"], contour_geojson)
    grids = {a.shape for a in (nir_arr, green_arr, blue_arr)} | {
        t for t in (nir_transform, green_transform, blue_transform)
    }
    if len(grids) != 2:  # одна форма + один transform — иначе окно не совпало
        raise ValueError("каналы b02/b03/b04/b08 на разных сетках — окно не совпадает")

    scl = _read_scl_grid(assets, contour_geojson, red_transform, red_crs, red_arr.shape)
    from app.services.sentinel_ingest.ndvi import CLOUD_SCL_VALUES

    cloud = np.isin(scl, CLOUD_SCL_VALUES)

    # прореживание окна до MAX_SIDE_PX (ближайший сосед, трансформ × f)
    factor = max(1, math.ceil(max(red_arr.shape) / MAX_SIDE_PX))
    sl = (slice(None, None, factor), slice(None, None, factor))
    from rasterio.transform import Affine

    transform = red_transform * Affine.scale(factor, factor)
    nir_d = nir_arr[sl]
    red_d = red_arr[sl]
    green_d = green_arr[sl]
    blue_d = blue_arr[sl]
    cloud_d = cloud[sl]

    geom_raster = transform_geom("EPSG:4326", red_crs, contour_geojson)
    inside = contour_mask(geom_raster, transform, nir_d.shape)

    # --- вариант ndvi: палитра; облака и вне контура прозрачны -----------
    ndvi = compute_index_array(INDEX_NAME, nir_d.astype("float32"), red_d.astype("float32"))
    t = np.clip((ndvi - _NDVI_LO) / (_NDVI_HI - _NDVI_LO), 0.0, 1.0)
    t[~np.isfinite(ndvi)] = 0.0
    alpha_ndvi = np.where(inside & np.isfinite(ndvi) & ~cloud_d, 255, 0).astype("uint8")
    png_ndvi = _png_rgba(_palette_rgb(t), alpha_ndvi)

    # --- вариант truecolor: композит B04/B03/B02, перцентильная нормировка
    lo, hi = np.percentile(
        np.concatenate(
            [a[inside].ravel() for a in (red_d, green_d, blue_d) if a[inside].size]
        ),
        (_TC_LO_PCT, _TC_HI_PCT),
    )
    span = float(hi - lo) or 1.0

    def _stretch(arr: np.ndarray) -> np.ndarray:
        return np.clip((arr.astype("float32") - lo) / span * 255.0, 0, 255).astype("uint8")

    rgb_tc = np.stack([_stretch(red_d), _stretch(green_d), _stretch(blue_d)], axis=-1)
    alpha_tc = np.where(inside, 255, 0).astype("uint8")
    png_tc = _png_rgba(rgb_tc, alpha_tc)

    # bbox окна растра в WGS84 (для позиционирования оверлея, §7.3)
    left, top = transform * (0, 0)
    right, bottom = transform * (nir_d.shape[1], nir_d.shape[0])
    west, south, east, north = transform_bounds(
        str(red_crs), "EPSG:4326", left, bottom, right, top, densify_pts=21
    )
    bbox = tuple(round(v, 7) for v in (west, south, east, north))

    return SceneRender(
        png={"ndvi": png_ndvi, "truecolor": png_tc},
        width=nir_d.shape[1],
        height=nir_d.shape[0],
        bbox=bbox,  # type: ignore[arg-type]
    )


def write_renders(
    render_dir: Path | str, field_id, captured_at: date, render: SceneRender
) -> None:
    """Сохранить PNG обоих вариантов + сайдкар геопривязки (атомарно по датам)."""
    meta = meta_path(render_dir, field_id, captured_at)
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps(
            {
                "bbox": list(render.bbox),
                "width": render.width,
                "height": render.height,
            }
        ),
        encoding="utf-8",
    )
    for variant, png in render.png.items():
        path = render_path(render_dir, field_id, variant, captured_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)


__all__ = [
    "MAX_SIDE_PX",
    "PALETTE",
    "RENDER_VARIANTS",
    "SceneRender",
    "meta_path",
    "render_path",
    "render_scene",
    "renders_exist",
    "write_renders",
]
