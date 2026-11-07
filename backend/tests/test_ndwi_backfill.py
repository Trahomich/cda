"""Тесты бэкфилла NDWI-статистики исторических сцен (v3-12, #104, docs/06 §7.4).

Проверяется: сцена, сохранённая без b03 (ndwi NULL), после появления b03 в
активов строки каталога дозаполняется ndwi_mean/ndwi_p90 теми же значениями,
что даёт zonal_ndvi (тот же набор пикселей); сцены каталога без b03
остаются NULL (no_b03, не ошибка); повторный запуск ничего не меняет
(идемпотентность фильтром ndwi_p90 IS NULL); date_from отсекает старые сцены.
"""

from datetime import date

from app.models.integrations import SatelliteScene
from app.services.sentinel_ingest.ndvi import run_ndvi_cycle, zonal_ndvi
from app.services.sentinel_ingest.ndwi_backfill import run_ndwi_backfill
from sqlalchemy import select
from tests.test_satellite import make_satellite_source
from tests.test_sentinel_ndvi import (  # noqa: F401 — фикстура scene_rasters
    CRS,
    X0,
    Y1,
    make_catalog_row,
    make_ndvi_field,
    scene_rasters,
    wgs_box,
)


def _wet_b03_assets(rasters, tmp_path) -> dict:
    """Активы с «влажным» b03 (green > nir → NDWI > 0) на сетке b04/b08."""
    from rasterio.transform import Affine
    from tests.test_sentinel_ndvi import _write_tif

    nir = rasters["nir"]
    wet_green = (nir.astype("float32") * 1.6 + 300).astype("uint16")
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    b03 = _write_tif(tmp_path / "b03_wet.tif", wet_green, t10, CRS)
    return {**rasters["assets"], "b03": b03}


def _make_scene(db, tmp_path, assets) -> tuple:
    """Поле + строка каталога + прогон цикла → сцена с ndwi_p90 = NULL."""
    make_satellite_source(db)
    field_id = make_ndvi_field(db)
    item = make_catalog_row(db, field_id, assets)
    report = run_ndvi_cycle(db)
    assert report.status == "ok" and report.scenes == 1
    scene = db.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
    assert scene is not None and scene.ndwi_p90 is None  # сцена «до #91»
    return field_id, item, scene


class TestNdwiBackfill:
    def test_updates_scene_with_b03(self, db_session, scene_rasters, tmp_path):  # noqa: F811
        """Сцена без b03 → каталог получил b03 → бэкфилл дозаполняет колонки."""
        field_id, item, _ = _make_scene(db_session, tmp_path, scene_rasters["assets"])

        item.assets = _wet_b03_assets(scene_rasters, tmp_path)
        db_session.commit()

        report = run_ndwi_backfill(db_session)

        assert report.scenes == 1 and report.updated == 1
        assert not report.unavailable and report.failed == 0 and report.gaps == 0
        db_session.expire_all()
        scene = db_session.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
        expected = zonal_ndvi(item.assets, wgs_box(300100, 5760100, 301900, 5760900))
        assert scene.ndwi_mean == expected.ndwi_mean
        assert scene.ndwi_p90 == expected.ndwi_p90
        assert scene.ndwi_mean > 0  # «влажное» поле: green > nir
        # NDVI-колонки не пересчитываются — сцена уже принята по этим данным
        assert float(scene.index_avg) > 0

    def test_idempotent_rerun(self, db_session, scene_rasters, tmp_path):  # noqa: F811
        """Повторный запуск: обновлённая сцена больше не кандидат."""
        field_id, item, _ = _make_scene(db_session, tmp_path, scene_rasters["assets"])
        item.assets = _wet_b03_assets(scene_rasters, tmp_path)
        db_session.commit()

        first = run_ndwi_backfill(db_session)
        assert first.updated == 1

        second = run_ndwi_backfill(db_session)

        assert second.scenes == 0 and second.updated == 0  # ndwi_p90 уже не NULL

    def test_no_b03_stays_null(self, db_session, scene_rasters, tmp_path):  # noqa: F811
        """Каталог без b03: no_b03 в отчёте, сцена остаётся NULL, без ошибок."""
        field_id, _, _ = _make_scene(db_session, tmp_path, scene_rasters["assets"])

        report = run_ndwi_backfill(db_session)

        assert report.scenes == 1 and report.no_b03 == 1 and report.updated == 0
        assert not report.unavailable and report.failed == 0
        db_session.expire_all()
        scene = db_session.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
        assert scene.ndwi_mean is None and scene.ndwi_p90 is None

    def test_date_from_filters_scenes(self, db_session, scene_rasters, tmp_path):  # noqa: F811
        """date_from позже captured_at сцены — кандидататов нет."""
        field_id, item, _ = _make_scene(db_session, tmp_path, scene_rasters["assets"])
        item.assets = _wet_b03_assets(scene_rasters, tmp_path)
        db_session.commit()

        report = run_ndwi_backfill(db_session, date_from=date(2026, 11, 1))

        assert report.scenes == 0  # сцена от 2026-10-31 отфильтрована
