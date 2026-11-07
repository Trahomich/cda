"""Тесты PNG-рендеров сцены (docs/06 §7.3, задача #83).

Проверяется: render_scene по синтетическим GeoTIFF (b04/b08/b03/b02 10 м
+ scl 20 м) отдаёт PNG обоих вариантов (ndvi — палитра, облака и вне
контура прозрачны; truecolor — композит, вне контура прозрачен), размер
ограничен MAX_SIDE_PX, bbox ≈ окну контура; write_renders создаёт файлы
по контракту {field_id}/{variant}/{date}.png + meta/{date}.json; цикл
run_ndvi_cycle с render_dir пишет рендеры сохранённым сценам, а без
каналов b02/b03 (строки каталога до #83) сцена сохраняется без рендера и
без ошибки; API: GET render.png (image/png, X-Field-Bbox/X-Image-Size,
404 без рендера/даты, 422 неизвестный вариант), API-06 render_available.
"""

import json
import uuid
from datetime import date

import numpy as np
import pytest
from app.models.integrations import SatelliteScene
from app.services.sentinel_ingest.ndvi import run_ndvi_cycle
from app.services.sentinel_ingest.renders import (
    MAX_SIDE_PX,
    RENDER_VARIANTS,
    meta_path,
    render_path,
    render_scene,
    renders_exist,
)
from rasterio.transform import Affine
from sqlalchemy import select
from tests.test_satellite import make_satellite_source
from tests.test_sentinel_ndvi import (  # noqa: F401 — фикстура scene_rasters
    CRS,
    X0,
    Y1,
    N,
    make_catalog_row,
    make_ndvi_field,
    scene_rasters,
    wgs_box,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
API = "/api/v1"
SCENE_DATE = date(2026, 10, 31)


@pytest.fixture()
def color_rasters(scene_rasters, tmp_path):
    """К сцене добавляются b02/b03 (10 м): синий/зелёный слабее красного."""
    from tests.test_sentinel_ndvi import _write_tif

    rng = np.random.default_rng(11)
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    blue = rng.uniform(300, 900, (N, N)).astype("uint16")
    green = rng.uniform(400, 1200, (N, N)).astype("uint16")
    scene_rasters["assets"] = {
        **scene_rasters["assets"],
        "b02": _write_tif(tmp_path / "b02.tif", blue, t10, CRS),
        "b03": _write_tif(tmp_path / "b03.tif", green, t10, CRS),
    }
    return scene_rasters


class TestRenderScene:
    def test_both_variants_png_and_bbox(self, color_rasters):
        """Оба варианта — валидные PNG; bbox покрывает контур (окно ≥ контура)."""
        contour = wgs_box(300100, 5760100, 301900, 5760900)
        render = render_scene(color_rasters["assets"], contour)

        assert render is not None
        assert set(render.png) == set(RENDER_VARIANTS)
        for variant in RENDER_VARIANTS:
            assert render.png[variant].startswith(PNG_MAGIC)
        assert max(render.width, render.height) <= MAX_SIDE_PX

        from shapely.geometry import box as sbox

        assert sbox(*render.bbox).contains(sbox(*_contour_wgs_bounds(contour)))

    def test_ndvi_variant_masks_clouds_and_outside(self, color_rasters):
        """ndvi: альфа=0 у облаков (SCL 9) и вне контура; внутри — непрозрачно."""
        import rasterio
        from rasterio.io import MemoryFile

        # контур на всю плашку: верхняя половина — облако
        contour = wgs_box(300050, 5760050, 301950, 5761950)
        render = render_scene(color_rasters["assets"], contour)

        with MemoryFile(render.png["ndvi"]) as mem:
            with rasterio.open(mem) as ds:
                assert ds.count == 4 and ds.dtypes[0] == "uint8"
                alpha = ds.read(4)
        top = alpha[: alpha.shape[0] // 4]
        bottom = alpha[-alpha.shape[0] // 4:]
        assert not top.any()  # облачная зона прозрачна
        assert bottom.mean() > 200  # растительность непрозрачна

    def test_no_visible_bands_returns_none(self, scene_rasters):
        """Активов без b02/b03 (каталог до #83): рендер пропускается, без ошибки."""
        contour = wgs_box(300100, 5760100, 301900, 5760900)
        assert render_scene(scene_rasters["assets"], contour) is None


def _contour_wgs_bounds(contour: dict) -> tuple[float, float, float, float]:
    from shapely.geometry import shape

    return shape(contour).bounds


class TestCycleRenders:
    def test_scene_renders_written(self, db_session, color_rasters, tmp_path):
        """Сохранённой сцене пишутся оба PNG + сайдкар геопривязки."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, field_id, color_rasters["assets"])

        report = run_ndvi_cycle(db_session, render_dir=str(tmp_path))

        assert report.status == "ok" and report.scenes == 1
        for variant in RENDER_VARIANTS:
            path = render_path(tmp_path, field_id, variant, SCENE_DATE)
            assert path.is_file(), path
            assert path.read_bytes().startswith(PNG_MAGIC)
        meta_file = meta_path(tmp_path, field_id, SCENE_DATE)
        meta = json.loads(meta_file.read_text())
        assert len(meta["bbox"]) == 4
        assert meta["width"] > 0 and meta["height"] > 0
        assert renders_exist(tmp_path, field_id, SCENE_DATE)

    def test_scene_saved_without_render_bands(self, db_session, scene_rasters, tmp_path):
        """Нет b02/b03 в активах: сцена сохраняется, рендеров нет, ошибок нет."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, field_id, scene_rasters["assets"])

        report = run_ndvi_cycle(db_session, render_dir=str(tmp_path))

        assert report.status == "ok" and report.scenes == 1
        assert not renders_exist(tmp_path, field_id, SCENE_DATE)

    def test_cloudy_scene_no_render(self, db_session, color_rasters, tmp_path):
        """Облачность > 60: сцена не сохраняется — рендера тоже нет."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session, name="Облачное-2")
        from sqlalchemy import text as db_text

        db_session.execute(
            db_text(
                "UPDATE dat_field SET contour = ST_GeomFromGeoJSON(:geo)::geography WHERE id = :id"
            ),
            {"id": str(field_id), "geo": json.dumps(wgs_box(300100, 5761100, 301900, 5761900))},
        )
        db_session.commit()
        make_catalog_row(db_session, field_id, color_rasters["assets"])

        report = run_ndvi_cycle(db_session, render_dir=str(tmp_path))

        assert report.cloud_skipped == 1
        assert not renders_exist(tmp_path, field_id, SCENE_DATE)


@pytest.fixture()
def render_client(client, tmp_path):
    """Клиент API с каталогом рендеров во временном каталоге (§7.3)."""
    from app.core.config import get_settings

    app = client.app
    settings = get_settings().model_copy(update={"ndvi_render_dir": str(tmp_path)})
    app.dependency_overrides[get_settings] = lambda: settings
    yield client, tmp_path
    app.dependency_overrides.pop(get_settings, None)


class TestRenderApi:
    @pytest.fixture(autouse=True)
    def _seed_scene(self, db_session, render_client, color_rasters):
        """Сцена с рендерами + сцена без (историческая, без b02/b03)."""
        from app.models.auth import UserRole
        from tests.test_auth import auth_header, make_user

        make_user(db_session, login="sat-agro", role=UserRole.AGRONOMIST, password="pw-sat")
        self.client, self.render_dir = render_client
        self.auth = auth_header(self.client, "sat-agro", "pw-sat")
        make_satellite_source(db_session)

        self.field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, self.field_id, color_rasters["assets"])
        report = run_ndvi_cycle(db_session, render_dir=str(self.render_dir))
        assert report.scenes == 1

        # историческая сцена другого поля — без рендеров
        self.old_field_id = make_ndvi_field(db_session, name="Старое-1")
        from datetime import timedelta

        item = make_catalog_row(
            db_session, self.old_field_id, color_rasters["assets"], item_id="OLD_SCENE"
        )
        item.captured_at = SCENE_DATE - timedelta(days=10)
        db_session.commit()
        # расчёт с отключённым каталогом рендеров → сцена есть, PNG нет
        run_ndvi_cycle(db_session)
        assert db_session.scalar(
            select(SatelliteScene.id).where(SatelliteScene.field_id == self.old_field_id)
        )

    def test_render_png_ok(self):
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi/{SCENE_DATE.isoformat()}/render.png",
            headers=self.auth,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(PNG_MAGIC)
        bbox = [float(v) for v in resp.headers["X-Field-Bbox"].split(",")]
        assert len(bbox) == 4 and bbox[0] < bbox[2] and bbox[1] < bbox[3]
        w, h = resp.headers["X-Image-Size"].split("x")
        assert int(w) > 0 and int(h) > 0
        assert "immutable" in resp.headers["cache-control"]

    def test_variant_truecolor(self):
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi/{SCENE_DATE.isoformat()}/render.png",
            headers=self.auth,
            params={"variant": "truecolor"},
        )
        assert resp.status_code == 200
        assert resp.content.startswith(PNG_MAGIC)

    def test_unknown_variant_422(self):
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi/{SCENE_DATE.isoformat()}/render.png",
            headers=self.auth,
            params={"variant": "rgb"},
        )
        assert resp.status_code == 422

    def test_scene_without_render_404(self):
        """Сцена есть, PNG нет (историческая): 404 render_not_found."""
        resp = self.client.get(
            f"{API}/fields/{self.old_field_id}/ndvi/2026-10-21/render.png",
            headers=self.auth,
        )
        assert resp.status_code == 404

    def test_unknown_date_404(self):
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi/2020-01-01/render.png",
            headers=self.auth,
        )
        assert resp.status_code == 404

    def test_unknown_field_404(self):
        resp = self.client.get(
            f"{API}/fields/{uuid.uuid4()}/ndvi/{SCENE_DATE.isoformat()}/render.png",
            headers=self.auth,
        )
        assert resp.status_code == 404

    def test_timeline_render_available_flag(self):
        """API-06: render_available=true у сцены с рендером, false у исторической."""
        for field_id, expected in ((self.field_id, True), (self.old_field_id, False)):
            resp = self.client.get(f"{API}/fields/{field_id}/ndvi", headers=self.auth)
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert items and all(item["render_available"] is expected for item in items)
