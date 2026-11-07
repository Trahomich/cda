"""Тесты бэкфилла исторических NDVI-сцен (#108, docs/06 §7.5).

Проверяется: сквозной инжест прошлых сезонов (каталог STAC → NDVI+рендеры,
сцены в dat_satellite_scene за каждый год диапазона --years); фильтр
item'ов без полного набора каналов (b02/b03/b04/b08/scl — пропуск
assets_skipped, не падение); облачность каталога >60 не каталогизируется;
идемпотентность повторного прогона (UQ каталога и сцен, per_year = 0);
инвалидация кэша insights:*/vra:peak:* затронутых полей; окна по кварталам
с обрезкой будущего; недоступность STAC — окно в catalog_failed, прогон
не падает. Наружу тесты не ходят: STAC — FakeStacClient, COG — локальные
GeoTIFF, кэш — monkeypatch; БД — DATABASE_URL_TEST.
"""

import json
import uuid
from datetime import date

import numpy as np
import pytest
from app.integrations import SourceUnavailableError
from app.integrations.stac import FakeStacClient, StacItem
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.services.sentinel_ingest.history_backfill import (
    HistoryBackfillReport,
    quarter_windows,
    run_history_backfill,
)
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping
from sqlalchemy import select
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_satellite import make_satellite_source

CRS = "EPSG:32637"  # UTM 37N — как тайлы Sentinel-2 на юго-запад от Воронежа
X0, Y1 = 300000.0, 5762000.0  # левый-верх плашки, 200×200 пикселей по 10 м
N = 200
TODAY = date(2026, 11, 7)


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
def band_tiffs(tmp_path):
    """Синтетическая плашка: b02/b03/b04/b08 (10 м) + scl (20 м, без облаков)."""
    rng = np.random.default_rng(11)
    nir = rng.uniform(2000, 4000, (N, N)).astype("uint16")
    red = rng.uniform(400, 1200, (N, N)).astype("uint16")
    green = rng.uniform(500, 1500, (N, N)).astype("uint16")
    blue = rng.uniform(400, 1300, (N, N)).astype("uint16")
    scl = np.full((N, N), 4, dtype="uint8")  # растительность, облаков нет
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y1)
    return {
        "b02": _write_tif(tmp_path / "b02.tif", blue, t10, CRS),
        "b03": _write_tif(tmp_path / "b03.tif", green, t10, CRS),
        "b04": _write_tif(tmp_path / "b04.tif", red, t10, CRS),
        "b08": _write_tif(tmp_path / "b08.tif", nir, t10, CRS),
        "scl": _write_tif(tmp_path / "scl.tif", scl, t20, CRS),
    }


def make_history_field(db: Session, name="Историческое-1") -> uuid.UUID:
    """Поле-контур в центре синтетической плашки (WGS84)."""
    field_id = uuid.uuid4()
    utm_box = box(X0 + 80000, Y1 - 100000, X0 + 100000, Y1 - 80000)
    geo = transform_geom(CRS, "EPSG:4326", mapping(utm_box))
    db.execute(
        db_text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, 'active')"
        ),
        {"id": field_id, "name": name, "geo": json.dumps(geo)},
    )
    db.commit()
    return field_id


def footprint() -> dict:
    """Footprint плашки STAC (WGS84): накрывает контур поля с запасом."""
    return transform_geom(CRS, "EPSG:4326", mapping(box(X0, Y1 - 200000, X0 + 200000, Y1)))


def stac_item(item_id: str, captured: date, assets: dict, *, cloud=5.0) -> StacItem:
    return StacItem(
        item_id=item_id,
        captured_at=captured,
        cloud_pct=cloud,
        tile_id="37UFT",
        geometry=footprint(),
        assets=assets,
    )


def all_bands_items(bands: dict) -> list[StacItem]:
    """Даты прошлых сезонов + граничные случаи: вне глубины, облачная, без b03."""
    return [
        stac_item("S2_20240615", date(2024, 6, 15), bands),
        stac_item("S2_20250510", date(2025, 5, 10), bands),
        stac_item("S2_20250820", date(2025, 8, 20), bands),
        stac_item("S2_20260110", date(2026, 1, 10), bands),
        stac_item("S2_20220701", date(2022, 7, 1), bands),  # глубже years=3
        stac_item("S2_20250602", date(2025, 6, 2), bands, cloud=90.0),  # >60 в каталог
        stac_item(
            "S2_20250603",  # нет b03 — рендеры/NDWI невозможны, пропуск
            date(2025, 6, 3),
            {k: v for k, v in bands.items() if k != "b03"},
        ),
    ]


# =============================================================================
# Окна каталога по кварталам
# =============================================================================


class TestQuarterWindows:
    def test_four_quarters_full_year(self):
        windows = quarter_windows(2025, date(2026, 11, 7))
        assert windows == [
            (date(2025, 1, 1), date(2025, 3, 31)),
            (date(2025, 4, 1), date(2025, 6, 30)),
            (date(2025, 7, 1), date(2025, 9, 30)),
            (date(2025, 10, 1), date(2025, 12, 31)),
        ]

    def test_current_year_cut_to_today(self):
        assert quarter_windows(2026, TODAY) == [
            (date(2026, 1, 1), date(2026, 3, 31)),
            (date(2026, 4, 1), date(2026, 6, 30)),
            (date(2026, 7, 1), date(2026, 9, 30)),
            (date(2026, 10, 1), TODAY),
        ]

    def test_future_year_empty(self):
        assert quarter_windows(2027, TODAY) == []


# =============================================================================
# Сквозной бэкфилл
# =============================================================================


class TestRunHistoryBackfill:
    def test_ingests_all_years_in_depth(self, db_session, band_tiffs, tmp_path):
        field_id = make_history_field(db_session)
        make_satellite_source(db_session)
        render_dir = tmp_path / "renders"

        report = run_history_backfill(
            db_session,
            client=FakeStacClient(all_bands_items(band_tiffs)),
            years=3,  # 2024–2026 при today=2026-11-07
            render_dir=str(render_dir),
            today=TODAY,
        )

        # каталог: 4 чистых item'а с полным набором каналов (глубина 3 года)
        assert report.catalog_scenes == 4 and report.catalog_new_rows == 4
        assert report.assets_skipped == 1  # item без b03
        assert report.catalog_failed == []

        # сцены: по одной за 2024/2026 и две за 2025; других годов нет
        per_year = {
            int(y): n
            for y, n in db_session.execute(
                db_text(
                    "SELECT extract(year from captured_at)::int, count(*) "
                    "FROM dat_satellite_scene WHERE index='ndvi' GROUP BY 1"
                )
            ).fetchall()
        }
        assert per_year == {2024: 1, 2025: 2, 2026: 1}
        assert report.per_year == {2024: 1, 2025: 2, 2026: 1}

        # строки каталога доведены до processed; NDWI посчитан по b03
        statuses = db_session.execute(
            select(SatelliteCatalogItem.status, SatelliteCatalogItem.item_id)
        ).fetchall()
        assert len(statuses) == 4
        assert {s for s, _ in statuses} == {"processed"}
        scene = db_session.scalar(
            select(SatelliteScene).where(SatelliteScene.captured_at == date(2025, 5, 10))
        )
        assert scene is not None and scene.field_id == field_id
        assert scene.ndwi_mean is not None and scene.ndwi_p90 is not None

        # рендеры исторической сцены: ndvi/truecolor/ndwi (docs/06 §7.3)
        from app.services.sentinel_ingest.renders import render_path

        for variant in ("ndvi", "truecolor", "ndwi"):
            assert render_path(render_dir, field_id, variant, date(2025, 5, 10)).exists()

        # кэш затронутых полей инвалидирован (здесь — счётчик полей)
        assert report.fields == 1

    def test_idempotent_second_run(self, db_session, band_tiffs, tmp_path):
        make_history_field(db_session)
        make_satellite_source(db_session)
        client = FakeStacClient(all_bands_items(band_tiffs))
        first = run_history_backfill(
            db_session, client=client, years=3, render_dir=str(tmp_path / "r"), today=TODAY
        )
        second = run_history_backfill(
            db_session, client=client, years=3, render_dir=str(tmp_path / "r"), today=TODAY
        )

        assert sum(first.per_year.values()) == 4
        assert second.catalog_new_rows == 0
        assert second.per_year == {2024: 0, 2025: 0, 2026: 0}
        assert second.scenes_processed == 0
        total = db_session.execute(
            db_text("SELECT count(*) FROM dat_satellite_scene WHERE index='ndvi'")
        ).scalar_one()
        assert total == 4  # дублей нет (UQ field_id, index, captured_at, source_id)

    def test_cache_invalidated_for_active_fields(self, db_session, band_tiffs, monkeypatch):
        field_id = make_history_field(db_session)
        make_satellite_source(db_session)
        deleted: list[str] = []
        monkeypatch.setattr(
            "app.services.sentinel_ingest.history_backfill.cache_delete",
            lambda *patterns: deleted.extend(patterns) or len(patterns),
        )

        report = run_history_backfill(
            db_session,
            client=FakeStacClient(all_bands_items(band_tiffs)),
            years=3,
            today=TODAY,
        )

        assert report.cache_deleted == 3  # insights:*, vra:peak, vra:peak:*
        assert f"insights:{field_id}:*" in deleted
        assert f"vra:peak:{field_id}" in deleted
        assert f"vra:peak:{field_id}:*" in deleted  # сезонные ключи #110

    def test_stac_unavailable_window_logged_not_fatal(self, db_session, band_tiffs):
        make_history_field(db_session)
        make_satellite_source(db_session)

        class UnavailableClient:
            def search(self, bbox, date_from, date_to, *, max_cloud_pct=None):
                raise SourceUnavailableError("STAC недоступен (HTTP 503)")

        report = run_history_backfill(db_session, client=UnavailableClient(), years=1, today=TODAY)
        assert report.catalog_failed  # все окна в ошибках
        assert len(report.catalog_failed) == report.windows
        assert report.per_year == {2026: 0}
        # прогон не упал, кэш всё равно инвалидирован по активным полям
        assert report.fields == 1

    def test_no_fields_no_cache_hits(self, db_session, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "app.services.sentinel_ingest.history_backfill.cache_delete",
            lambda *patterns: calls.extend(patterns) or 0,
        )
        report = run_history_backfill(db_session, client=FakeStacClient([]), years=1, today=TODAY)
        assert report.fields == 0 and not calls
        assert report.as_dict()["catalog_failed_count"] == 0


# =============================================================================
# Отчёт
# =============================================================================


class TestReport:
    def test_as_dict_counts(self):
        report = HistoryBackfillReport(
            years=[2024, 2025], catalog_failed=["a"], scenes_unavailable=["b", "c"]
        )
        data = report.as_dict()
        assert data["catalog_failed_count"] == 1
        assert data["scenes_unavailable_count"] == 2
        assert data["years"] == [2024, 2025]
