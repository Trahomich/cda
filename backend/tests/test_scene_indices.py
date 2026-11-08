"""Тесты индексов §7.7 (docs/06-data-api.md, #128): NDRE/NDMI/NDSI/EVI/SAVI/BSI.

Формулы на синтетических массивах; расчёт по синтетическим GeoTIFF
(b02/b03/b04/b08 10 м + b05/b11/scl 20 м, как scene_rasters в
test_sentinel_ndvi, дополненные полосами); запись сцен-индексов в
dat_satellite_scene (UQ, констрейнт индексов); API-06 path-форма
/fields/{id}/{index} и ?index=; рендеры вариантов (в т.ч. NDSI/BSI
палитры); идемпотентность CLI scene-indices-backfill. COG — локальные
файлы, БД — DATABASE_URL_TEST, наружу тесты не ходят.
"""

import uuid
from datetime import date

import numpy as np
import pytest
from app.integrations.raster import (
    INDEX_BOUNDS,
    INDICES,
    compute_bsi_array,
    compute_evi_array,
    compute_index_array,
    validate_index_values,
)
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.indices import (
    EXTRA_INDICES,
    IndexStat,
    compute_scene_indices,
    upsert_scene_indices,
)
from app.services.sentinel_ingest.indices_backfill import run_indices_backfill
from rasterio.transform import Affine
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_satellite_source
from tests.test_sentinel_ndvi import _write_tif, make_ndvi_field, wgs_box

CRS = "EPSG:32637"
X0, Y1 = 300000.0, 5762000.0
N = 200
DAY = date(2026, 10, 31)
API = "/api/v1"


@pytest.fixture()
def band_rasters(tmp_path):
    """Сцена со всеми полосами: b02/b03/b04/b08 (10 м), b05/b11/scl (20 м).

    Северная четверть плашки — облако (SCL 9); поле целиком в южной
    (безоблачной) половине, как в test_sentinel_ndvi.
    """
    rng = np.random.default_rng(11)
    nir = rng.uniform(2000, 4000, (N, N)).astype("uint16")
    red = rng.uniform(400, 1400, (N, N)).astype("uint16")
    green = rng.uniform(600, 1600, (N, N)).astype("uint16")
    blue = rng.uniform(400, 1200, (N, N)).astype("uint16")
    rededge = rng.uniform(1500, 3000, (N // 2, N)).astype("uint16")
    swir = rng.uniform(800, 2200, (N // 2, N)).astype("uint16")
    scl = np.full((N // 2, N), 4, dtype="uint8")
    scl[: N // 4, :] = 9

    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y1)
    bands = {
        "b02": _write_tif(tmp_path / "b02.tif", blue, t10, CRS),
        "b03": _write_tif(tmp_path / "b03.tif", green, t10, CRS),
        "b04": _write_tif(tmp_path / "b04.tif", red, t10, CRS),
        "b08": _write_tif(tmp_path / "b08.tif", nir, t10, CRS),
        "b05": _write_tif(tmp_path / "b05.tif", rededge, t20, CRS),
        "b11": _write_tif(tmp_path / "b11.tif", swir, t20, CRS),
        "scl": _write_tif(tmp_path / "scl.tif", scl, t20, CRS),
    }
    return {"assets": bands, "nir": nir, "red": red, "green": green, "blue": blue}


# -- формулы (§7.7) ---------------------------------------------------------


class TestFormulas:
    def test_normalized_differences(self) -> None:
        """ndre/ndmi/ndsi — нормализованные разности по своим полосам."""
        nir = np.array([[3000.0, 2000.0], [1000.0, 0.0]], dtype="float32")
        other = np.array([[1000.0, 2000.0], [3000.0, 0.0]], dtype="float32")
        arr = compute_index_array("ndre", nir, other)
        assert arr[0, 0] == pytest.approx(0.5)
        assert arr[0, 1] == pytest.approx(0.0)
        assert arr[1, 0] == pytest.approx(-0.5)
        assert np.isnan(arr[1, 1])  # 0/0 — нет данных

    def test_evi_formula(self) -> None:
        """EVI = 2.5·(NIR−RED)/(NIR+6·RED−7.5·BLUE+1) (§7.7)."""
        nir = np.array([3000.0], dtype="float32")
        red = np.array([1000.0], dtype="float32")
        blue = np.array([500.0], dtype="float32")
        expected = 2.5 * (3000 - 1000) / (3000 + 6 * 1000 - 7.5 * 500 + 1)
        assert compute_evi_array(nir, red, blue)[0] == pytest.approx(expected)

    def test_bsi_formula(self) -> None:
        """BSI = ((SWIR+RED)−(NIR+BLUE))/((SWIR+RED)+(NIR+BLUE)) (§7.7)."""
        nir = np.array([2000.0], dtype="float32")
        red = np.array([800.0], dtype="float32")
        blue = np.array([600.0], dtype="float32")
        swir = np.array([2400.0], dtype="float32")
        soil, veg = swir + red, nir + blue
        expected = (soil - veg) / (soil + veg)
        assert compute_bsi_array(nir, red, blue, swir)[0] == pytest.approx(expected)

    def test_evi_bounds_wider(self) -> None:
        """EVI валидируется в расширенном диапазоне (§7.7), не [−1; 1]."""
        values = np.array([2.2], dtype="float32")
        validate_index_values("evi", values)  # не бросает
        with pytest.raises(Exception, match="evi"):
            validate_index_values("evi", np.array([3.5], dtype="float32"))
        assert INDEX_BOUNDS["evi"] == (-1.0, 3.0)

    def test_indices_set(self) -> None:
        assert set(INDICES) == {"ndvi", "ndwi", "ndre", "ndmi", "ndsi", "evi", "savi", "bsi"}


# -- расчёт по растрам ------------------------------------------------------


class TestComputeSceneIndices:
    def test_all_indices_from_bands(self, band_rasters) -> None:
        """Все 7 индексов §7.7 считаются по полосам (20 м ресемплятся)."""
        contour = wgs_box(300100, 5760100, 301900, 5760900)
        stats = compute_scene_indices(band_rasters["assets"], contour)
        names = {st.index for st in stats}
        assert names == set(EXTRA_INDICES)
        by_index = {st.index: st for st in stats}
        # ndre/ndmi > 0 на растительности (nir доминирует), bsi < 0
        assert by_index["ndre"].index_avg > 0
        assert by_index["ndmi"].index_avg > 0
        assert by_index["ndsi"].index_avg < 0  # зелёная растительность, не снег
        assert by_index["evi"].index_avg > 0
        assert by_index["savi"].index_avg > 0
        assert by_index["bsi"].index_avg < 0
        # значения в диапазонах валидации
        for st in stats:
            lo, hi = INDEX_BOUNDS.get(st.index, (-1.0, 1.0))
            assert lo <= st.index_min <= st.index_max <= hi

    def test_missing_bands_skip_indices(self, band_rasters) -> None:
        """Нет b05/b11 — индексы с ними пропускаются, остальные считаются."""
        assets = {k: v for k, v in band_rasters["assets"].items() if k not in ("b05", "b11")}
        contour = wgs_box(300100, 5760100, 301900, 5760900)
        names = {st.index for st in compute_scene_indices(assets, contour)}
        assert names == {"ndwi", "evi", "savi"}

    def test_upsert_idempotent(self, band_rasters, db_session) -> None:
        """UQ(field, index, date, source): повторный upsert не дублирует."""
        from tests.test_satellite import make_satellite_source

        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        stats = [IndexStat("ndre", 0.1, 0.25, 0.4, 5.0), IndexStat("bsi", -0.4, -0.1, 0.1, 5.0)]
        assert upsert_scene_indices(db_session, field_id, DAY, stats) == 2
        db_session.commit()
        assert upsert_scene_indices(db_session, field_id, DAY, stats) == 0
        db_session.commit()
        count = db_session.scalar(
            db_text("SELECT count(*) FROM dat_satellite_scene WHERE field_id = :fid"),
            {"fid": field_id},
        )
        assert count == 2


# -- инжест: индексы вместе с NDVI ------------------------------------------


class TestNdviCycleIndices:
    def test_cycle_writes_index_scenes(self, db_session, band_rasters) -> None:
        """run_ndvi_cycle: вместе с ndvi пишутся сцены всех индексов §7.7."""
        from app.services.sentinel_ingest.ndvi import run_ndvi_cycle

        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        db_session.add(
            SatelliteCatalogItem(
                item_id="S2_37UFT_IDX1",
                field_id=field_id,
                captured_at=DAY,
                tile_id="37UFT",
                cloud_pct=5.0,
                assets=band_rasters["assets"],
                status="cataloged",
            )
        )
        db_session.commit()

        report = run_ndvi_cycle(db_session, render_dir=None)
        assert report.scenes == 1 and report.index_scenes == len(EXTRA_INDICES)
        indexes = set(
            db_session.scalars(
                db_text("SELECT DISTINCT \"index\" FROM dat_satellite_scene").columns()
            )
        )
        assert {"ndvi", *EXTRA_INDICES} <= indexes


# -- бэкфилл (#128) ---------------------------------------------------------


class TestIndicesBackfill:
    def _seed(self, db_session, assets) -> uuid.UUID:
        """ndvi-сцена без индексов + строка каталога с теми же активами."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        db_session.add(
            SatelliteScene(
                field_id=field_id,
                index="ndvi",
                captured_at=DAY,
                resolution_m=10,
                index_avg=0.5,
                index_min=0.2,
                index_max=0.7,
                cloud_pct=5.0,
                source_id="satellite.sentinel2-l2a",
                trust=0.95,
            )
        )
        db_session.add(
            SatelliteCatalogItem(
                item_id="S2_37UFT_BF1",
                field_id=field_id,
                captured_at=DAY,
                tile_id="37UFT",
                cloud_pct=5.0,
                assets=assets,
                status="processed",
            )
        )
        db_session.commit()
        return field_id

    def test_backfill_writes_and_idempotent(self, db_session, band_rasters) -> None:
        field_id = self._seed(db_session, band_rasters["assets"])

        report = run_indices_backfill(db_session, render_dir=None)
        assert report.scenes == 1 and report.scenes_written == 1
        assert report.index_scenes == len(EXTRA_INDICES)
        assert sum(report.per_index.values()) == len(EXTRA_INDICES)

        # повтор — кандидатов нет, ничего не меняется
        report2 = run_indices_backfill(db_session, render_dir=None)
        assert report2.scenes == 0 and report2.index_scenes == 0
        total = db_session.scalar(
            db_text("SELECT count(*) FROM dat_satellite_scene WHERE field_id = :fid"),
            {"fid": field_id},
        )
        assert total == 1 + len(EXTRA_INDICES)

    def test_backfill_without_bands_partial(self, db_session, band_rasters) -> None:
        """Старые активы без b05/b11 (без STAC-клиента) — только 10-м индексы."""
        assets = {
            k: v for k, v in band_rasters["assets"].items() if k not in ("b05", "b11")
        }
        self._seed(db_session, assets)
        report = run_indices_backfill(db_session, render_dir=None)
        assert report.scenes_written == 1
        assert report.index_scenes == 3  # ndwi/evi/savi
        assert report.partial_bands == 1


# -- API-06 (path-форма §7.7) -----------------------------------------------


def _api_headers(client, db_session) -> dict:
    from app.models.auth import UserRole

    make_user(db_session, login="idx_agro", role=UserRole.AGRONOMIST, password="secret123")
    return auth_header(client, "idx_agro", "secret123")


class TestIndexApi:
    def test_path_form_all_indices(self, client, db_session, band_rasters) -> None:
        """/fields/{id}/{index} отдаёт таймлайн каждого индекса §7.7."""
        self._seed(db_session, band_rasters["assets"])
        headers = _api_headers(client, db_session)
        field_id = db_session.scalar(db_text("SELECT id FROM dat_field LIMIT 1"))
        for name in EXTRA_INDICES:
            resp = client.get(f"{API}/fields/{field_id}/{name}", headers=headers)
            assert resp.status_code == 200, (name, resp.text)
            body = resp.json()
            assert body["index"] == name and len(body["items"]) == 1
            assert body["items"][0]["index"] == name

    def test_query_param_form_unknown_422(self, client, db_session) -> None:
        make_ndvi_field(db_session)
        headers = _api_headers(client, db_session)
        field_id = db_session.scalar(db_text("SELECT id FROM dat_field LIMIT 1"))
        resp = client.get(
            f"{API}/fields/{field_id}/ndvi",
            params={"index": "lai"},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "unknown_index"

    def test_year_filter(self, client, db_session, band_rasters) -> None:
        self._seed(db_session, band_rasters["assets"])
        run_indices_backfill(db_session, render_dir=None)
        headers = _api_headers(client, db_session)
        field_id = db_session.scalar(db_text("SELECT id FROM dat_field LIMIT 1"))
        resp = client.get(
            f"{API}/fields/{field_id}/ndre",
            params={"year": 2025},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0  # сцена 2026 года
        resp2 = client.get(
            f"{API}/fields/{field_id}/ndre", params={"year": 2026}, headers=headers
        )
        assert resp2.json()["total"] == 1


# -- рендеры вариантов §7.7 --------------------------------------------------


class TestIndexRenders:
    def test_render_variants_written(self, tmp_path, band_rasters) -> None:
        """render_scene: 9 вариантов, в т.ч. NDSI (белый снег) и BSI (почва)."""
        from app.services.sentinel_ingest.renders import (
            PALETTE_BSI,
            PALETTE_NDSI,
            RENDER_VARIANTS,
            render_scene,
            write_renders,
        )

        contour = wgs_box(300100, 5760100, 301900, 5760900)
        render = render_scene(band_rasters["assets"], contour)
        assert render is not None
        assert set(render.png) == set(RENDER_VARIANTS)
        field_id = uuid.uuid4()
        write_renders(tmp_path, field_id, DAY, render)
        assert all(
            (tmp_path / str(field_id) / variant / f"{DAY.isoformat()}.png").is_file()
            for variant in RENDER_VARIANTS
        )
        # палитры снега/почвы различаются (не растительность-зелёный)
        assert PALETTE_NDSI[2][1] == (255, 255, 255)
        assert PALETTE_BSI[2][1] == (139, 90, 43)

    def test_render_without_swir_bands(self, tmp_path, band_rasters) -> None:
        """Нет b05/b11 — рендеры остальных вариантов строятся, SWIR-слоёв нет."""
        from app.services.sentinel_ingest.renders import render_scene

        assets = {
            k: v for k, v in band_rasters["assets"].items() if k not in ("b05", "b11")
        }
        render = render_scene(assets, wgs_box(300100, 5760100, 301900, 5760900))
        assert render is not None
        assert set(render.png) == {"ndvi", "truecolor", "ndwi", "evi", "savi"}
