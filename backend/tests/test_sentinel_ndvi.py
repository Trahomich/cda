"""Тесты расчёта NDVI по COG-активам сцен (v1-05, #51, docs/05 §2 v1).

Проверяется: зональная статистика NDVI по синтетическим GeoTIFF (b04/b08
10 м UTM + scl 20 м) совпадает с ручным расчётом; маскирование облаков
SCL ∈ {8,9,10} и честная облачность полезной площади; правила 05 §5 п.2
(≤20 обычная, 20–60 серым/сохраняется, >60 не сохраняется); цикл
run_ndvi_cycle (строки cataloged → processed/failed, сцены в
dat_satellite_scene с trust = 1 − cloud/100, дедуп UQ, недоступность COG —
строка ждёт повтора + журнал error, DataGap — failed). Наружу тесты не
ходят: COG — локальные файлы; БД — DATABASE_URL_TEST.
"""

import json
import uuid
from datetime import date

import numpy as np
import pytest
from app.integrations.raster import compute_index_array
from app.models.integrations import SatelliteCatalogItem, SatelliteScene, SourceJournalEntry
from app.services.sentinel_ingest.ndvi import (
    INDEX,
    MAX_SCENE_CLOUD_PCT,
    NdviStats,
    run_ndvi_cycle,
    zonal_ndvi,
)
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping
from sqlalchemy import select
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_satellite import make_satellite_source

CRS = "EPSG:32637"  # UTM 37N — тайл Sentinel-2 на юго-запад от Воронежа
X0, Y1 = 300000.0, 5762000.0  # левый-верх сетки, 200×200 пикселей по 10 м
N = 200


def _write_tif(path, arr, transform, crs):
    import rasterio

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=arr.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(arr, 1)
    return str(path)


@pytest.fixture()
def scene_rasters(tmp_path):
    """Синтетическая сцена: b04/b08 (10 м), scl (20 м); верхняя половина — облако."""
    rng = np.random.default_rng(7)
    nir = rng.uniform(1500, 4000, (N, N)).astype("uint16")
    red = rng.uniform(500, 1500, (N, N)).astype("uint16")
    scl = np.full((N // 2, N), 4, dtype="uint8")  # растительность
    scl = np.vstack([np.full((N // 2, N), 9, dtype="uint8"), scl])

    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y1)
    return {
        "b04": _write_tif(tmp_path / "b04.tif", red, t10, CRS),
        "b08": _write_tif(tmp_path / "b08.tif", nir, t10, CRS),
        "scl": _write_tif(tmp_path / "scl.tif", scl, t20, CRS),
        "nir": nir,
        "red": red,
    }


def wgs_box(xmin, ymin, xmax, ymax) -> dict:
    """Прямоугольник UTM → GeoJSON в WGS84 (контур поля хранится в 4326)."""
    return transform_geom(CRS, "EPSG:4326", mapping(box(xmin, ymin, xmax, ymax)))


def make_ndvi_field(db: Session, name="Заречное-1", status="active") -> uuid.UUID:
    """Поле внутри синтетической плашки (нижняя половина — без облака)."""
    field_id = uuid.uuid4()
    geo = json.dumps(wgs_box(300100, 5760100, 301900, 5760900))
    db.execute(
        db_text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, :status)"
        ),
        {"id": field_id, "name": name, "geo": geo, "status": status},
    )
    db.commit()
    return field_id


def make_catalog_row(
    db: Session, field_id: uuid.UUID, assets: dict, *, item_id="S2A_37UFT_20261031", cloud=5.0
) -> SatelliteCatalogItem:
    item = SatelliteCatalogItem(
        item_id=item_id,
        field_id=field_id,
        captured_at=date(2026, 10, 31),
        tile_id="37UFT",
        cloud_pct=cloud,
        assets=assets,
        status="cataloged",
    )
    db.add(item)
    db.commit()
    return item


# =============================================================================
# Зональная статистика: сверка с ручным расчётом
# =============================================================================


class TestZonalNdvi:
    def test_stats_match_manual(self, scene_rasters):
        """Статистика (min/avg/max/std) совпадает с ручным расчётом по пикселям."""
        contour = wgs_box(300100, 5760100, 301900, 5760900)  # нижняя половина
        stats = zonal_ndvi(scene_rasters, contour)

        ndvi = compute_index_array(
            INDEX, scene_rasters["nir"].astype("float32"), scene_rasters["red"].astype("float32")
        )
        expected = ndvi[100:200, 10:190]  # тот же квадрат в пикселях сетки
        assert stats.index_min == round(float(expected.min()), 3)
        assert stats.index_avg == round(float(expected.mean()), 3)
        assert stats.index_max == round(float(expected.max()), 3)
        assert stats.std == round(float(expected.std()), 3)
        assert stats.cloud_pct == 0.0

    def test_cloud_masked_and_honest_cloud_pct(self, scene_rasters):
        """Пиксели SCL∈{8,9,10} исключены из статистики; cloud_pct — доля
        замаскированных пикселей полезной площади (весь растр → 50 %)."""
        contour = wgs_box(300050, 5760050, 301950, 5761950)
        stats = zonal_ndvi(scene_rasters, contour)

        assert stats.cloud_pct == 50.0
        ndvi = compute_index_array(
            INDEX, scene_rasters["nir"].astype("float32"), scene_rasters["red"].astype("float32")
        )
        expected = ndvi[100:200, :]  # нижняя половина: облако верхней замаскировано
        assert stats.index_avg == round(float(expected.mean()), 3)
        assert stats.index_min == round(float(expected.min()), 3)
        assert stats.index_max == round(float(expected.max()), 3)

    def test_field_fully_cloud_returns_empty_stats(self, scene_rasters):
        """Облачность > 60: статистика не считается (сцене не сохраняться)."""
        contour = wgs_box(300100, 5761100, 301900, 5761900)  # верхняя половина
        stats = zonal_ndvi(scene_rasters, contour)
        assert stats.cloud_pct == 100.0 > MAX_SCENE_CLOUD_PCT
        assert stats == NdviStats(0.0, 0.0, 0.0, 0.0, 100.0)


# =============================================================================
# Цикл расчёта по каталогу: статусы, сцены, журнал (05 §5 п.2)
# =============================================================================


class TestNdviCycle:
    def test_scene_written_and_row_processed(self, db_session, scene_rasters):
        """Каталог cataloged → processed; сцена ndvi с trust = 1 − cloud/100."""
        from app.modules.satellite.router import SATELLITE_SOURCE_ID

        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        item = make_catalog_row(db_session, field_id, scene_rasters)

        report = run_ndvi_cycle(db_session)

        assert report.status == "ok"
        assert report.scenes == 1
        scene = db_session.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
        assert scene is not None
        assert scene.index == "ndvi"
        assert scene.resolution_m == 10
        assert scene.captured_at == date(2026, 10, 31)
        assert float(scene.cloud_pct) == 0.0
        assert float(scene.trust) == 1.0
        assert scene.source_id == SATELLITE_SOURCE_ID
        assert 0.0 <= float(scene.index_avg) <= 1.0

        db_session.refresh(item)
        assert item.status == "processed"
        assert item.error is None
        assert item.processed_at is not None

    def test_repeat_run_no_duplicates(self, db_session, scene_rasters):
        """Повторный цикл: processed-строки не обрабатываются, дублей сцен нет."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        make_catalog_row(db_session, field_id, scene_rasters)
        run_ndvi_cycle(db_session)

        report = run_ndvi_cycle(db_session)

        assert report.skipped is not None
        count = len(
            db_session.execute(
                select(SatelliteScene.id).where(SatelliteScene.field_id == field_id)
            ).all()
        )
        assert count == 1

    def test_cloud_over_60_scene_not_saved(self, db_session, scene_rasters):
        """Честная облачность > 60 (05 §5 п.2): сцена не сохраняется, строка processed."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session, name="Облачное-1")
        # контур поля — верхняя (облачная) половина плашки
        db_session.execute(
            db_text(
                "UPDATE dat_field SET contour = ST_GeomFromGeoJSON(:geo)::geography "
                "WHERE id = :id"
            ),
            {"id": str(field_id), "geo": json.dumps(wgs_box(300100, 5761100, 301900, 5761900))},
        )
        db_session.commit()
        item = make_catalog_row(db_session, field_id, scene_rasters)

        report = run_ndvi_cycle(db_session)

        assert report.cloud_skipped == 1
        assert report.scenes == 0
        assert (
            db_session.scalar(
                select(SatelliteScene.id).where(SatelliteScene.field_id == field_id)
            )
            is None
        )
        db_session.refresh(item)
        assert item.status == "processed"  # это не ошибка — повторять нечего

    def test_cloud_20_to_60_saved_with_gray_trust(self, db_session, scene_rasters):
        """Облачность 20–60: сцена сохраняется, trust = 1 − cloud/100 < 1."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session, name="Половинчатое-1")
        db_session.execute(
            db_text(
                "UPDATE dat_field SET contour = ST_GeomFromGeoJSON(:geo)::geography "
                "WHERE id = :id"
            ),
            {"id": str(field_id), "geo": json.dumps(wgs_box(300050, 5760050, 301950, 5761950))},
        )
        db_session.commit()
        make_catalog_row(db_session, field_id, scene_rasters)

        run_ndvi_cycle(db_session)

        scene = db_session.scalar(select(SatelliteScene).where(SatelliteScene.field_id == field_id))
        assert scene is not None
        assert float(scene.cloud_pct) == 50.0
        assert float(scene.trust) == 0.5

    def test_missing_assets_row_failed(self, db_session):
        """Нет COG-активов → DataGap: строка failed с причиной, без сцены."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        item = make_catalog_row(db_session, field_id, {"b04": "/tmp/x.tif"})

        report = run_ndvi_cycle(db_session)

        assert report.gaps == 1
        db_session.refresh(item)
        assert item.status == "failed"
        assert "нет каналов" in (item.error or "")

    def test_cog_unavailable_row_waits_and_journal_error(self, db_session):
        """COG недоступен (файла нет): строка остаётся cataloged, журнал error."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        item = make_catalog_row(
            db_session,
            field_id,
            {"b04": "/tmp/nope_b04.tif", "b08": "/tmp/nope_b08.tif", "scl": "/tmp/nope_scl.tif"},
        )

        report = run_ndvi_cycle(db_session)

        assert report.status == "error"
        assert len(report.unavailable) == 1
        db_session.refresh(item)
        assert item.status == "cataloged"  # повтор в следующем цикле
        journal = (
            db_session.execute(
                select(SourceJournalEntry)
                .where(SourceJournalEntry.status == "error")
                .order_by(SourceJournalEntry.at.desc())
            )
            .scalars()
            .first()
        )
        assert journal is not None
        assert "COG недоступен" in (journal.reason or "")

    def test_inactive_field_row_failed(self, db_session, scene_rasters):
        """Поле неактивно: контур не находится, строка failed с причиной."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session, name="Заброшенное-1", status="archived")
        item = make_catalog_row(db_session, field_id, scene_rasters)

        report = run_ndvi_cycle(db_session)

        assert report.gaps == 1
        db_session.refresh(item)
        assert item.status == "failed"
        assert "неактивно" in (item.error or "")

    def test_batch_limit_leaves_rows_cataloged(self, db_session, scene_rasters):
        """Батч: за цикл обрабатывается не больше batch_limit строк."""
        make_satellite_source(db_session)
        field_id = make_ndvi_field(db_session)
        for i in range(3):
            make_catalog_row(
                db_session, field_id, scene_rasters, item_id=f"S2A_37UFT_2026102{i}"
            )

        report = run_ndvi_cycle(db_session, batch_limit=2)

        assert report.rows == 2
        remaining = (
            db_session.execute(
                select(SatelliteCatalogItem).where(SatelliteCatalogItem.status == "cataloged")
            )
            .scalars()
            .all()
        )
        assert len(remaining) == 1

    def test_empty_catalog_idle_no_journal(self, db_session):
        """Нет cataloged-строк: цикл idle, журнал не трогаем."""
        report = run_ndvi_cycle(db_session)
        assert report.status == "skipped"
        assert (
            db_session.execute(select(SourceJournalEntry)).scalars().first() is None
        )


# =============================================================================
# Настройки воркера
# =============================================================================


class TestWorkerSettings:
    def test_ndvi_settings_defaults(self):
        from app.core.config import Settings

        settings = Settings()
        assert settings.ingest_satellite_ndvi_batch == 200
        assert settings.ingest_satellite_ndvi_workers == 4

    def test_worker_docstring_mentions_ndvi(self):
        from app.services.sentinel_ingest import worker

        assert "ndvi" in worker.__doc__.lower()
        assert hasattr(worker, "_ndvi_cycle")


# фикстура datetime используется для сверки processed_at ниже при желании
_ = (UTC, datetime)
