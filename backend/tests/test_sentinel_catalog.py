"""Тесты каталогизации Sentinel-2 через STAC (v1-04 #50, IF-SATELLITE).

Проверяется: парсинг item'ов earth-search (метаданные, tile, активы COG),
пагинация по link rel=next, фильтр облачности >60 % (05 §5 п.2), ошибки
5xx/сети → SourceUnavailableError; цикл каталогизации (bbox хозяйства →
сопоставление контуров PostGIS → upsert dat_satellite_catalog, дедуп UQ,
журнал API-28 ok/degraded/error без исключений наружу); фейк-клиент;
планировщик воркера (интервал каталога по 05 §4). Наружу тесты не ходят:
STAC — httpx.MockTransport; БД — PostgreSQL (DATABASE_URL_TEST).
"""

import json
import uuid
from datetime import date, timedelta

import httpx
import pytest
from app.core.config import Settings
from app.integrations import SourceUnavailableError
from app.integrations.stac import (
    CATALOG_ASSETS,
    COLLECTION,
    SATELLITE_SOURCE_ID,
    EarthSearchStacClient,
    FakeStacClient,
    StacItem,
)
from app.models.integrations import SatelliteCatalogItem, SourceJournalEntry
from app.models.references import Source
from app.services.sentinel_ingest.catalog import (
    BBOX_PAD_DEG,
    farm_bbox,
    intersecting_field_ids,
    run_catalog_cycle,
)
from app.services.sentinel_ingest.worker import JOB_CATALOG, catalog_window, create_worker
from sqlalchemy import select
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_satellite import make_satellite_source

# Контур в координатах тайла 37UFT (Воронежская обл., как демо-данные)
FIELD_GEO = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [
            [
                [41.25, 52.02],
                [41.36, 52.02],
                [41.36, 52.08],
                [41.25, 52.08],
                [41.25, 52.02],
            ]
        ],
    }
)


def make_stac_field(db: Session, name="Овражное-1", status="active") -> uuid.UUID:
    field_id = uuid.uuid4()
    db.execute(
        db_text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, :status)"
        ),
        {"id": field_id, "name": name, "geo": FIELD_GEO, "status": status},
    )
    db.commit()
    return field_id


def stac_feature(
    item_id: str,
    *,
    lon=41.30,
    lat=52.05,
    cloud=5.0,
    tile="37UFT",
    captured="2026-10-31",
    mgrs_prop="s2:mgrs_tile",
) -> dict:
    """Feature STAC как отдаёт earth-search (footprint 0.02° вокруг центра)."""
    d = 0.01
    return {
        "id": item_id,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - d, lat - d],
                    [lon + d, lat - d],
                    [lon + d, lat + d],
                    [lon - d, lat + d],
                    [lon - d, lat - d],
                ]
            ],
        },
        "properties": {
            "datetime": f"{captured}T09:53:27Z",
            "eo:cloud_cover": cloud,
            mgrs_prop: tile,
        },
        "assets": {
            band: {"href": f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/{item_id}/{band}.tif"}
            for band in CATALOG_ASSETS
        },
    }


def mock_client(handler) -> EarthSearchStacClient:
    """Клиент на MockTransport: наружу тесты не ходят."""
    return EarthSearchStacClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


WINDOW = (date(2026, 10, 17), date(2026, 10, 31))


# =============================================================================
# STAC-клиент: парсинг, фильтры, пагинация, ошибки
# =============================================================================


class TestStacClient:
    def test_request_shape_and_parse(self):
        """POST /search с коллекцией, bbox, окном и query облачности."""
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200, json={"features": [stac_feature("S2A_37UFT_20261031_0_L2A")]}
            )

        items = mock_client(handler).search([41.24, 52.02, 41.36, 52.08], *WINDOW)
        assert len(items) == 1
        body = bodies[0]
        assert body["collections"] == [COLLECTION]
        assert body["bbox"] == [41.24, 52.02, 41.36, 52.08]
        assert body["datetime"].startswith("2026-10-17T00:00:00Z")
        assert body["datetime"].endswith("2026-10-31T23:59:59Z")
        assert body["query"]["eo:cloud_cover"]["lte"] == 60.0

        item = items[0]
        assert isinstance(item, StacItem)
        assert item.item_id == "S2A_37UFT_20261031_0_L2A"
        assert item.captured_at == date(2026, 10, 31)
        assert item.cloud_pct == 5.0
        assert item.tile_id == "37UFT"
        for band in CATALOG_ASSETS:
            assert item.asset(band).endswith(f"/{band}.tif")

    def test_esa_asset_names_mapped(self):
        """earth-search отдаёт ESA-имена red/nir → канонические b04/b08."""

        def handler(request: httpx.Request) -> httpx.Response:
            feature = stac_feature("S2C_37UFT_20261031_0_L2A")
            feature["assets"] = {
                "red": {"href": "https://x/red.tif"},
                "nir": {"href": "https://x/nir.tif"},
                "scl": {"href": "https://x/scl.tif"},
                "blue": {"href": "https://x/blue.tif"},
            }
            return httpx.Response(200, json={"features": [feature]})

        item = mock_client(handler).search([0, 0, 1, 1], *WINDOW)[0]
        assert item.assets == {
            "b04": "https://x/red.tif",
            "b08": "https://x/nir.tif",
            "scl": "https://x/scl.tif",
        }

    def test_tile_from_grid_code(self):
        """tile из grid:code без префикса MGRS-, если нет s2:mgrs_tile."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "features": [stac_feature("S2B_37UFT_20261029_0_L2A", mgrs_prop="grid:code")]
                },
            )

        items = mock_client(handler).search([0, 0, 1, 1], *WINDOW)
        assert items[0].tile_id == "37UFT"

    def test_feature_without_cloud_skipped(self):
        """item без даты или облачности бесполезен (05 §2) — пропускается."""
        feature = stac_feature("S2A_37UFT_20261031_0_L2A")
        del feature["properties"]["eo:cloud_cover"]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": [feature]})

        assert mock_client(handler).search([0, 0, 1, 1], *WINDOW) == []

    def test_pagination_by_next_link(self):
        """Страницы собираются по GET-ссылке rel=next с токеном."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if "token=abc" in str(request.url):
                return httpx.Response(
                    200, json={"features": [stac_feature("S2B_37UFT_20261030_0_L2A")]}
                )
            return httpx.Response(
                200,
                json={
                    "features": [stac_feature("S2A_37UFT_20261031_0_L2A")],
                    "links": [
                        {
                            "rel": "next",
                            "href": "https://stac.test/v1/search?token=abc",
                        }
                    ],
                },
            )

        items = mock_client(handler).search([0, 0, 1, 1], *WINDOW)
        assert [i.item_id for i in items] == [
            "S2A_37UFT_20261031_0_L2A",
            "S2B_37UFT_20261030_0_L2A",
        ]
        assert len(calls) == 2 and "token=abc" in calls[1]

    def test_5xx_is_source_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="boom")

        with pytest.raises(SourceUnavailableError):
            mock_client(handler).search([0, 0, 1, 1], *WINDOW)

    def test_network_error_is_source_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("нет сети")

        with pytest.raises(SourceUnavailableError):
            mock_client(handler).search([0, 0, 1, 1], *WINDOW)


# =============================================================================
# Фейк-клиент (CONTRACTS §7)
# =============================================================================


class TestFakeClient:
    def test_deterministic_and_cloud_filter(self):
        items = [
            StacItem("a", date(2026, 10, 31), 5.0, "37UFT", {}),
            StacItem("b", date(2026, 10, 30), 90.0, "37UFT", {}),
        ]
        fake = FakeStacClient(items)
        first = fake.search([0, 0, 1, 1], *WINDOW)
        second = fake.search([0, 0, 1, 1], *WINDOW)
        assert first == second and fake.calls == 2
        assert [i.item_id for i in first] == ["a"]  # >60 % отфильтрован


# =============================================================================
# Геопривязка: bbox хозяйства и пересечение контуров
# =============================================================================


class TestGeoMatching:
    def test_farm_bbox_pads_active_fields(self, db_session):
        make_stac_field(db_session)
        bbox = farm_bbox(db_session)
        assert bbox is not None
        assert bbox[0] == pytest.approx(41.25 - BBOX_PAD_DEG, abs=1e-6)
        assert bbox[3] == pytest.approx(52.08 + BBOX_PAD_DEG, abs=1e-6)

    def test_farm_bbox_none_without_fields(self, db_session):
        assert farm_bbox(db_session) is None  # только неактивные/нет полей

    def test_intersecting_fields(self, db_session):
        inside = make_stac_field(db_session, "Овражное-1")
        make_stac_field(db_session, "Далёкое-2", status="archived")
        geometry = stac_feature("x", lon=41.30, lat=52.05)["geometry"]
        assert intersecting_field_ids(db_session, geometry) == [inside]

    def test_far_scene_intersects_nothing(self, db_session):
        make_stac_field(db_session)
        geometry = stac_feature("x", lon=39.0, lat=50.0)["geometry"]
        assert intersecting_field_ids(db_session, geometry) == []


# =============================================================================
# Цикл каталогизации: upsert, дедуп, журнал источника (API-28)
# =============================================================================


def last_source_state(db):
    source = db.scalar(select(Source).where(Source.source_id == SATELLITE_SOURCE_ID))
    journal = db.scalars(select(SourceJournalEntry).order_by(SourceJournalEntry.at)).all()
    return source, journal


class TestCatalogCycle:
    def test_catalog_scenes_for_intersecting_field(self, db_session):
        field_id = make_stac_field(db_session)
        make_satellite_source(db_session)
        client = FakeStacClient(
            [
                StacItem(
                    item_id="S2C_37UFT_20261031_0_L2A",
                    captured_at=date(2026, 10, 31),
                    cloud_pct=3.6,
                    tile_id="37UFT",
                    geometry=stac_feature("x")["geometry"],
                    assets={"b04": "s3://b04", "b08": "s3://b08", "scl": "s3://scl"},
                )
            ]
        )
        report = run_catalog_cycle(db_session, client, date_from=WINDOW[0], date_to=WINDOW[1])
        assert report.status == "ok"
        assert report.scenes == 1 and report.fields == 1 and report.new_rows == 1

        row = db_session.scalar(select(SatelliteCatalogItem))
        assert row.field_id == field_id
        assert row.item_id == "S2C_37UFT_20261031_0_L2A"
        assert row.status == "cataloged" and row.processed_at is None
        assert float(row.cloud_pct) == pytest.approx(3.6)
        assert row.assets == {"b04": "s3://b04", "b08": "s3://b08", "scl": "s3://scl"}

        source, journal = last_source_state(db_session)
        assert source.last_status == "ok" and source.last_success_at is not None
        assert journal[-1].status == "ok"

    def test_dedup_second_cycle_no_new_rows(self, db_session):
        make_stac_field(db_session)
        make_satellite_source(db_session)
        item = StacItem("dup", date(2026, 10, 31), 5.0, "37UFT", stac_feature("x")["geometry"])
        client = FakeStacClient([item])
        first = run_catalog_cycle(db_session, client, date_from=WINDOW[0], date_to=WINDOW[1])
        second = run_catalog_cycle(db_session, client, date_from=WINDOW[0], date_to=WINDOW[1])
        assert first.new_rows == 1 and second.new_rows == 0
        assert second.status == "ok"
        rows = db_session.scalars(select(SatelliteCatalogItem)).all()
        assert len(rows) == 1  # UQ (field_id, item_id) защищает от дублей

    def test_scene_away_from_fields_not_cataloged(self, db_session):
        make_stac_field(db_session)
        make_satellite_source(db_session)
        client = FakeStacClient(
            [
                StacItem(
                    "far",
                    date(2026, 10, 30),
                    5.0,
                    "37UFT",
                    stac_feature("x", lon=39.0, lat=50.0)["geometry"],
                )
            ]
        )
        report = run_catalog_cycle(db_session, client, date_from=WINDOW[0], date_to=WINDOW[1])
        assert report.new_rows == 0
        assert db_session.scalars(select(SatelliteCatalogItem)).all() == []

    def test_unavailable_source_journal_error_no_exception(self, db_session):
        make_stac_field(db_session)
        make_satellite_source(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="недоступен")

        client = mock_client(handler)
        report = run_catalog_cycle(db_session, client, date_from=WINDOW[0], date_to=WINDOW[1])
        assert report.status == "error" and report.scenes == 0

        source, journal = last_source_state(db_session)
        assert source.last_status == "error" and source.last_success_at is None
        assert journal[-1].status == "error" and "503" in journal[-1].reason

    def test_empty_catalog_degraded(self, db_session):
        make_stac_field(db_session)
        make_satellite_source(db_session)
        client = FakeStacClient([])
        report = run_catalog_cycle(db_session, client, date_from=WINDOW[0], date_to=WINDOW[1])
        assert report.status == "degraded"
        source, _ = last_source_state(db_session)
        assert source.last_status == "degraded"

    def test_no_fields_cycle_skipped(self, db_session):
        make_satellite_source(db_session)
        report = run_catalog_cycle(db_session, FakeStacClient([]), date_from=WINDOW[0], date_to=WINDOW[1])
        assert report.status == "skipped" and report.skipped
        source, _ = last_source_state(db_session)
        assert source.last_status == "degraded"


# =============================================================================
# Воркер: окно каталога и расписание планировщика
# =============================================================================


class TestWorker:
    def test_catalog_window_backfill_days(self):
        settings = Settings(ingest_satellite_backfill_days=14)
        date_from, date_to = catalog_window(settings)
        assert date_from == date_to - timedelta(days=14)

    def test_scheduler_job_interval(self):
        settings = Settings(ingest_satellite_catalog_hours=6)
        scheduler = create_worker(settings)
        job = scheduler.get_job(JOB_CATALOG)
        assert job is not None
        assert job.trigger.interval == timedelta(hours=6)
        # планировщик не запускался (start) — воркер не тестируем живьём
