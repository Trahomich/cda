"""Тесты NDWI в конвейере сцены и флага вымокания (docs/06 §7.4, задача #91).

Проверяется: расчёт ndwi_mean/ndwi_p90 по синтетическому COG с каналом b03
совпадает с ручным (по тем же пикселям, что NDVI: контур, маска SCL); без
b03 в активах (строки каталога до #83) сцена сохраняется, NDWI-колонки
NULL — не ошибка; рендер variant=ndwi: валидный PNG с палитрой
«жёлтый → бирюзовый → синий», облака/вне контура прозрачны; цикл пишет
{field_id}/ndwi/{date}.png; API render.png?variant=ndwi (200) и 404 для
даты без ndwi-рендера; API-06 waterlogged: bool | null по правилу
ndwi_p90 ≥ 0.10 ∧ ndvi_mean < 0.45 ∧ осадки 7 сут ≥ 20 мм.
"""

import json
import uuid
from datetime import date, timedelta

import numpy as np
import pytest
from app.integrations.raster import compute_ndwi_array
from app.models.integrations import SatelliteScene, WeatherObservation
from app.services.sentinel_ingest.ndvi import run_ndvi_cycle, zonal_ndvi
from app.services.sentinel_ingest.renders import (
    PALETTE_NDWI,
    render_path,
    render_scene,
)
from rasterio.io import MemoryFile
from sqlalchemy import select
from tests.test_auth import auth_header, make_user
from tests.test_ndvi_renders import SCENE_DATE, color_rasters  # noqa: F401
from tests.test_satellite import make_satellite_source
from tests.test_sentinel_ndvi import make_catalog_row, make_ndvi_field, scene_rasters  # noqa: F401
from tests.test_sentinel_ndvi import wgs_box
from tests.test_weather import make_weather_source

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
API = "/api/v1"
CONTOUR = None  # задаётся фикстурой wat_scene


@pytest.fixture()
def ndwi_scene_rasters(color_rasters, tmp_path):  # noqa: F811
    """К сцене #83 (b02/b03 добавлены) b03 заменяется на «влажный» канал.

    green > nir по всей плашке → NDWI > 0 (влажное поле), NDVI остаётся как был.
    """
    from rasterio.transform import Affine
    from tests.test_sentinel_ndvi import CRS, X0, Y1, _write_tif

    nir = color_rasters["nir"]
    wet_green = (nir.astype("float32") * 1.6 + 300).astype("uint16")  # green > nir → NDWI > 0
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    color_rasters["assets"] = {
        **color_rasters["assets"],
        "b03": _write_tif(tmp_path / "b03_wet.tif", wet_green, t10, CRS),
    }
    color_rasters["green"] = wet_green
    return color_rasters


# =============================================================================
# Расчёт NDWI в зональной статистике
# =============================================================================


class TestZonalNdwi:
    def test_ndwi_stats_match_manual(self, ndwi_scene_rasters):
        """ndwi_mean/ndwi_p90 совпадают с ручным расчётом по тем же пикселям."""
        contour = wgs_box(300100, 5760100, 301900, 5760900)  # нижняя половина — без облака
        stats = zonal_ndvi(ndwi_scene_rasters["assets"], contour)

        ndwi = compute_ndwi_array(
            ndwi_scene_rasters["green"].astype("float32"),
            ndwi_scene_rasters["nir"].astype("float32"),
        )
        expected = ndwi[110:190, 10:190]  # те же пиксели, что у NDVI (тесты #51)
        assert stats.cloud_pct == 0.0
        assert stats.ndwi_mean == round(float(expected.mean()), 3)
        assert stats.ndwi_p90 == round(float(np.percentile(expected, 90)), 3)
        assert stats.ndwi_mean > 0  # поле «влажное»: green > nir

    def test_no_b03_stats_none(self, scene_rasters):  # noqa: F811
        """Активов без b03 (каталог до #83): NDWI = None, сцена без ошибок."""
        stats = zonal_ndvi(scene_rasters["assets"], wgs_box(300100, 5760100, 301900, 5760900))
        assert stats.ndwi_mean is None
        assert stats.ndwi_p90 is None
        assert stats.cloud_pct == 0.0  # NDVI при этом посчитан

    def test_cloudy_scene_no_ndwi(self, ndwi_scene_rasters):
        """Облачность > 60: статистики (и NDWI в т.ч.) нет — сцена не сохраняется."""
        contour = wgs_box(300100, 5761100, 301900, 5761900)  # верхняя половина — облако
        stats = zonal_ndvi(ndwi_scene_rasters["assets"], contour)
        assert stats.cloud_pct > 60
        assert stats.ndwi_mean is None


class TestCycleNdwi:
    def test_scene_stores_ndwi_columns(self, db_session, ndwi_scene_rasters):
        """Цикл пишет ndwi_mean/ndwi_p90 в dat_satellite_scene при наличии b03."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, field_id, ndwi_scene_rasters["assets"])

        report = run_ndvi_cycle(db_session)

        assert report.status == "ok" and report.scenes == 1
        scene = db_session.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
        assert scene.ndwi_mean is not None and scene.ndwi_p90 is not None
        assert float(scene.ndwi_p90) >= float(scene.ndwi_mean)

    def test_scene_without_b03_ndwi_null(self, db_session, scene_rasters):  # noqa: F811
        """Без b03: сцена сохраняется, ndwi-колонки NULL (не ошибка)."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, field_id, scene_rasters["assets"])

        report = run_ndvi_cycle(db_session)

        assert report.status == "ok" and report.scenes == 1
        scene = db_session.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
        assert scene.ndwi_mean is None and scene.ndwi_p90 is None


# =============================================================================
# Рендер variant=ndwi
# =============================================================================


def _read_png_rgba(png: bytes) -> tuple[np.ndarray, np.ndarray]:
    with MemoryFile(png) as mem:
        import rasterio

        with rasterio.open(mem) as ds:
            assert ds.count == 4 and ds.dtypes[0] == "uint8"
            return ds.read(), ds.read(4)


class TestNdwiRender:
    def test_ndwi_png_palette_and_masks(self, ndwi_scene_rasters):
        """ndwi: валидный PNG; облака/вне контура прозрачны; палитра от жёлтого к синему."""
        contour = wgs_box(300050, 5760050, 301950, 5761950)
        render = render_scene(ndwi_scene_rasters["assets"], contour)

        assert render is not None and "ndwi" in render.png
        png = render.png["ndwi"]
        assert png.startswith(PNG_MAGIC)
        rgb, alpha = _read_png_rgba(png)

        top = alpha[: alpha.shape[0] // 4]
        bottom = alpha[-alpha.shape[0] // 4:]
        assert not top.any()  # облачная зона (SCL 9) прозрачна
        assert bottom.mean() > 200  # полезная площадь непрозрачна

        # влажные пиксели (green > nir → NDWI > 0 → t > 0.5) ближе к синему,
        # чем жёлтая точка палитры: b > r у почти всех непрозрачных пикселей
        visible = alpha > 0
        assert visible.any()
        blue_dominant = (rgb[2].astype(int) - rgb[0].astype(int)) > 10
        assert blue_dominant[visible].mean() > 0.9

    def test_palette_is_dry_to_water(self):
        """Опорные точки палитры: жёлтый → бирюзовый → синий (§7.4)."""
        dry, mid, water = [c for _, c in PALETTE_NDWI]
        assert dry[0] > dry[2]  # жёлтый: R > B
        assert mid[1] > mid[0] and mid[1] > mid[2]  # бирюзовый: G доминирует
        assert water[2] > water[0] and water[2] > water[1]  # синий: B доминирует

    def test_cycle_writes_ndwi_png(self, db_session, ndwi_scene_rasters, tmp_path):
        """Цикл с render_dir пишет {field_id}/ndwi/{date}.png + meta-сайдкар."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, field_id, ndwi_scene_rasters["assets"])

        report = run_ndvi_cycle(db_session, render_dir=str(tmp_path))

        assert report.scenes == 1
        path = render_path(tmp_path, field_id, "ndwi", SCENE_DATE)
        assert path.is_file() and path.read_bytes().startswith(PNG_MAGIC)
        meta = json.loads((tmp_path / str(field_id) / "meta" / f"{SCENE_DATE}.json").read_text())
        assert len(meta["bbox"]) == 4


# =============================================================================
# API: render.png?variant=ndwi и waterlogged в API-06
# =============================================================================


@pytest.fixture()
def wat_client(client, tmp_path):
    from app.core.config import get_settings

    app = client.app
    settings = get_settings().model_copy(update={"ndvi_render_dir": str(tmp_path)})
    app.dependency_overrides[get_settings] = lambda: settings
    yield client, tmp_path
    app.dependency_overrides.pop(get_settings, None)


def _seed_observation(db, field_id, observed_at, precip_mm):
    db.add(
        WeatherObservation(
            field_id=field_id,
            observed_at=observed_at,
            precipitation_mm=precip_mm,
            source_id="weather.open-meteo",
        )
    )


class TestNdwiApi:
    @pytest.fixture(autouse=True)
    def _seed(self, db_session, wat_client, ndwi_scene_rasters):
        """Поле с влажной сценой + осадки; поле со старой сценой (без b03/рендеров)."""
        from app.models.auth import UserRole

        make_user(db_session, login="ndwi-agro", role=UserRole.AGRONOMIST, password="pw-ndwi")
        self.client, self.render_dir = wat_client
        self.auth = auth_header(self.client, "ndwi-agro", "pw-ndwi")
        make_satellite_source(db_session)
        make_weather_source(db_session)

        # влажное поле: сцена с NDWI + рендеры, осадки 30 мм за 7 сут до даты
        self.field_id = make_ndvi_field(db_session, name="Влажное-1")
        make_catalog_row(db_session, self.field_id, ndwi_scene_rasters["assets"])
        report = run_ndvi_cycle(db_session, render_dir=str(self.render_dir))
        assert report.scenes == 1
        scene = db_session.scalar(
            select(SatelliteScene).where(SatelliteScene.field_id == self.field_id)
        )
        assert scene.ndwi_p90 is not None
        # привести NDVI/NDWI к срабатыванию правила: низкий ndvi_mean, высокий ndwi_p90
        scene.index_avg = 0.30  # < 0.45
        scene.ndwi_p90 = 0.42  # ≥ 0.10
        db_session.commit()
        for back in range(7):
            _seed_observation(db_session, self.field_id, SCENE_DATE - timedelta(days=back), 5.0)
        db_session.commit()

        # сухое поле: NDWI низкий → не вымокание
        self.dry_field_id = make_ndvi_field(db_session, name="Сухое-1")
        make_catalog_row(
            db_session, self.dry_field_id, ndwi_scene_rasters["assets"], item_id="DRY_SCENE"
        )
        assert run_ndvi_cycle(db_session, render_dir=str(self.render_dir)).scenes == 1
        dry = db_session.scalar(
            select(SatelliteScene).where(SatelliteScene.field_id == self.dry_field_id)
        )
        dry.ndwi_p90 = 0.05  # < 0.10
        db_session.commit()
        for back in range(7):
            _seed_observation(db_session, self.dry_field_id, SCENE_DATE - timedelta(days=back), 5.0)
        db_session.commit()

        # старое поле: сцена без b03 (нет NDWI) и без рендеров
        self.old_field_id = make_ndvi_field(db_session, name="Старое-2")
        self.old_date = SCENE_DATE - timedelta(days=10)
        item = make_catalog_row(
            db_session, self.old_field_id, scene_rasters_assets(ndwi_scene_rasters),
            item_id="OLD_NDWI",
        ) if False else make_catalog_row(
            db_session, self.old_field_id, _b03_only_less(ndwi_scene_rasters), item_id="OLD_NDWI"
        )
        item.captured_at = self.old_date
        db_session.commit()
        run_ndvi_cycle(db_session)  # без render_dir: сцена есть, PNG нет

    def test_render_variant_ndwi_ok(self):
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi/{SCENE_DATE.isoformat()}/render.png",
            headers=self.auth,
            params={"variant": "ndwi"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(PNG_MAGIC)
        assert "immutable" in resp.headers["cache-control"]

    def test_render_ndwi_404_without_png(self):
        """Сцена без ndwi-рендера (историческая): 404, а не ошибка 500."""
        resp = self.client.get(
            f"{API}/fields/{self.old_field_id}/ndvi/{self.old_date.isoformat()}/render.png",
            headers=self.auth,
            params={"variant": "ndwi"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "render_not_found"

    def test_waterlogged_true(self):
        """Все три условия выполнены → waterlogged=true (§7.4)."""
        resp = self.client.get(f"{API}/fields/{self.field_id}/ndvi", headers=self.auth)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items[0]["waterlogged"] is True

    def test_waterlogged_false_low_ndwi(self):
        """ndwi_p90 < 0.10 при осадках и низком NDVI → false (данных достаточно)."""
        resp = self.client.get(f"{API}/fields/{self.dry_field_id}/ndvi", headers=self.auth)
        items = resp.json()["items"]
        assert items[0]["waterlogged"] is False

    def test_waterlogged_null_no_precip(self, db_session):
        """Осадков нет в окне → waterlogged=null (данных нет)."""
        db_session.query(WeatherObservation).filter(
            WeatherObservation.field_id == self.field_id
        ).delete()
        db_session.commit()
        resp = self.client.get(f"{API}/fields/{self.field_id}/ndvi", headers=self.auth)
        items = resp.json()["items"]
        assert items[0]["waterlogged"] is None

    def test_waterlogged_null_no_ndwi(self):
        """Сцена без NDWI-статистики (каталог до #83) → waterlogged=null."""
        resp = self.client.get(f"{API}/fields/{self.old_field_id}/ndvi", headers=self.auth)
        items = resp.json()["items"]
        assert items[0]["waterlogged"] is None
        assert items[0]["render_available"] is False

    def test_waterlogged_false_low_precip(self, db_session):
        """Осадки < 20 мм → false."""
        db_session.query(WeatherObservation).filter(
            WeatherObservation.field_id == self.field_id
        ).delete()
        for back in range(7):
            _seed_observation(db_session, self.field_id, SCENE_DATE - timedelta(days=back), 1.0)
        db_session.commit()
        resp = self.client.get(f"{API}/fields/{self.field_id}/ndvi", headers=self.auth)
        items = resp.json()["items"]
        assert items[0]["waterlogged"] is False


def scene_rasters_assets(ndwi_scene_rasters):
    """Активы сцены без b03 (эмуляция строки каталога до #83)."""
    return {k: v for k, v in ndwi_scene_rasters["assets"].items() if k != "b03"}


def _b03_only_less(ndwi_scene_rasters):
    return scene_rasters_assets(ndwi_scene_rasters)
