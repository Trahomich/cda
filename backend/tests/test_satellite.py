"""Тесты модуля спутника (задача MVP-10, API-06, IF-SATELLITE).

Проверяется: вычисление NDVI/SAVI по синтетическому растру и контуру
(rasterio+shapely; статистика min/mean/max, валидация диапазона 05 §5),
адаптер Copernicus (поиск сцен через httpx.MockTransport, нормализация
метаданных, вычисление статистики по注入-каналам), фейк-адаптер
(детерминированность, облачность), ingest-слой (дедуп UQ, фильтр
cloud_pct > 60, событие scene.ingested в шину, журнал dat_source_journal),
API-06 и RBAC группы satellite. Наружу тесты не ходят; БД — PostgreSQL
(DATABASE_URL_TEST, паттерн #20).
"""

import json
import uuid
from datetime import date, timedelta

import httpx
import numpy as np
import pytest
from app.core import events
from app.core.config import Settings
from app.integrations import DataGapError, DataQualityError, SourceUnavailableError
from app.integrations.raster import (
    compute_index_array,
    contour_mask,
    stats_over_mask,
    validate_index_values,
)
from app.integrations.sentinel2 import FakeSentinel2Adapter, Sentinel2Adapter
from app.models.auth import UserRole
from app.models.integrations import SatelliteScene, SourceJournalEntry
from app.models.references import Source
from app.modules.satellite.ingest import (
    SCENE_INGESTED_EVENT,
    SatelliteIngest,
    contour_lookup_factory,
)
from app.modules.satellite.router import SATELLITE_SOURCE_ID
from app.modules.satellite.scheduler import create_satellite_scheduler
from rasterio.transform import Affine
from shapely.geometry import mapping, shape
from sqlalchemy import select
from sqlalchemy import text as db_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from shapely.geometry import mapping, shape
from tests.test_auth import auth_header, make_user

API = "/api/v1"
FIELD_SQUARE = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [
            [
                [40.0, 51.0],
                [40.01, 51.0],
                [40.01, 51.01],
                [40.0, 51.01],
                [40.0, 51.0],
            ]
        ],
    }
)


def make_field(db: Session, name="Степное-1", status="active") -> uuid.UUID:
    field_id = uuid.uuid4()
    db.execute(
        db_text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, :status)"
        ),
        {"id": field_id, "name": name, "geo": FIELD_SQUARE, "status": status},
    )
    db.commit()
    return field_id


def make_satellite_source(db: Session) -> None:
    db.add(Source(source_id=SATELLITE_SOURCE_ID, kind="satellite"))
    db.commit()


# =============================================================================
# Вычисление индексов по синтетическому растру и контуру
# =============================================================================


class TestRasterIndices:
    TRANSFORM = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 4.0)  # сетка 4×4, пиксель 1×1

    @staticmethod
    def _grids():
        nir_values = [0.2, 0.4, 0.6, 0.8]
        red = np.full((4, 4), 0.1, dtype="float32")
        nir = np.tile(np.array(nir_values, dtype="float32"), (4, 1))
        return nir, red, nir_values

    @staticmethod
    def _ndvi(v: float) -> float:
        return (v - 0.1) / (v + 0.1)

    def test_ndvi_stats_full_coverage(self):
        """Статистика по всему полю: NDVI = (nir − red)/(nir + red)."""
        nir, red, nir_values = self._grids()
        values = compute_index_array("ndvi", nir, red)
        expected = [self._ndvi(v) for v in nir_values]
        lo, mean, hi = stats_over_mask(values, np.ones((4, 4), dtype=bool))
        assert lo == round(min(expected), 3)
        assert hi == round(max(expected), 3)
        assert mean == round(float(np.mean(expected * 4)), 3)

    def test_stats_only_inside_contour(self):
        """Статистика — только по пикселям внутри контура (левая половина)."""
        nir, red, nir_values = self._grids()
        values = compute_index_array("ndvi", nir, red)
        contour = mapping(shape({"type": "Polygon", "coordinates": [[[0, 4], [2, 4], [2, 0], [0, 0], [0, 4]]]}))
        mask = contour_mask(contour, self.TRANSFORM, (4, 4))
        # all_touched: полигон [0..2] задевает колонки 0..2
        assert mask[:, 3].sum() == 0
        lo, mean, hi = stats_over_mask(values, mask)
        inside = [self._ndvi(v) for v in nir_values[:3]]
        assert hi == round(max(inside), 3)
        assert lo == round(min(inside), 3)

    def test_savi_formula(self):
        """SAVI = 1.5·(NIR−RED)/(NIR+RED+0.5) (05 §2, L=0.5)."""
        nir = np.array([[0.5]], dtype="float32")
        red = np.array([[0.2]], dtype="float32")
        assert compute_index_array("savi", nir, red)[0, 0] == pytest.approx(1.5 * 0.3 / 1.2)

    def test_ndvi_out_of_range_data_quality(self):
        """NDVI вне [−1; 1] → DataQualityError (05 §5, правило 4)."""
        nir = np.array([[0.5]], dtype="float32")
        red = np.array([[-0.2]], dtype="float32")
        values = compute_index_array("ndvi", nir, red)
        assert values[0, 0] > 1.0
        with pytest.raises(DataQualityError):
            validate_index_values("ndvi", values)

    def test_zero_denominator_is_nan(self):
        """Нулевая сумма каналов (вне плашки) → NaN, не inf."""
        nir = np.array([[0.0, 0.4]], dtype="float32")
        red = np.array([[0.0, 0.1]], dtype="float32")
        values = compute_index_array("ndvi", nir, red)
        assert np.isnan(values[0, 0]) and np.isfinite(values[0, 1])

    def test_unknown_index_rejected(self):
        with pytest.raises(ValueError):
            compute_index_array("evi", np.zeros((1, 1)), np.zeros((1, 1)))


# =============================================================================
# Фейк-адаптер: детерминированность и фильтры облачности
# =============================================================================


class TestFakeAdapter:
    WINDOW = (date(2026, 6, 1), date(2026, 6, 21))

    def test_deterministic(self):
        fake = FakeSentinel2Adapter()
        first = fake.fetch_scenes("550e8400-e29b-41d4-a716-446655440000", "ndvi", *self.WINDOW)
        second = fake.fetch_scenes("550e8400-e29b-41d4-a716-446655440000", "ndvi", *self.WINDOW)
        assert first == second
        assert first, "в окне 21 сут должны быть сцены"
        for scene in first:
            assert scene.cloud_pct <= 20.0  # порог по умолчанию
            assert -1.0 <= scene.index_min <= scene.index_avg <= scene.index_max <= 1.0
            assert scene.trust == round(1.0 - scene.cloud_pct / 100.0, 2)
            assert scene.source_id == SATELLITE_SOURCE_ID

    def test_cloud_above_threshold_filtered(self):
        fake = FakeSentinel2Adapter(forced_cloud_pct=70.0)
        assert fake.fetch_scenes("f" * 8 + "-0000-4000-8000-000000000001", "ndvi", *self.WINDOW) == []
        softer = FakeSentinel2Adapter(forced_cloud_pct=30.0)
        scenes = softer.fetch_scenes(
            "f" * 8 + "-0000-4000-8000-000000000001", "ndvi", *self.WINDOW, max_cloud_pct=40
        )
        assert scenes and all(s.cloud_pct == 30.0 for s in scenes)

    def test_unknown_index(self):
        with pytest.raises(ValueError):
            FakeSentinel2Adapter().fetch_scenes("x", "evi", *self.WINDOW)


# =============================================================================
# Адаптер Copernicus (HTTP мокается, каналы — синтетические массивы)
# =============================================================================

TOKEN_BODY = {"access_token": "test-token", "expires_in": 600}
SCENE_ID = "9c8d7e6f-0000-4000-8000-abcdefabcdef"


def _catalog_body(captured_at: str, cloud_pct: float) -> dict:
    return {
        "value": [
            {
                "Id": SCENE_ID,
                "Name": f"S2B_MSIL2A_{captured_at}_T37UCS",
                "ContentDate": {"Start": f"{captured_at}T09:23:11.024Z"},
                "Attributes": [{"Name": "cloudCover", "Value": cloud_pct}],
            }
        ]
    }


class TestSentinel2Adapter:
    @staticmethod
    def _client(handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    @staticmethod
    def _geometry_ok(field_id: str) -> dict | None:
        return json.loads(FIELD_SQUARE) if field_id == "ok" else None

    def _adapter(self, handler, raster_loader=None) -> Sentinel2Adapter:
        return Sentinel2Adapter(
            geometry_lookup=self._geometry_ok,
            http=self._client(handler),
            client_id="id",
            client_secret="secret",
            raster_loader=raster_loader or (lambda ref, band: (_raise(),))[0],
        )

    def test_no_keys_is_unavailable(self):
        adapter = Sentinel2Adapter(
            geometry_lookup=self._geometry_ok, http=self._client(lambda r: r)
        )
        with pytest.raises(SourceUnavailableError):
            adapter.search_scenes("ok", date(2026, 6, 1), date(2026, 6, 21))

    def test_no_contour_is_gap(self):
        adapter = Sentinel2Adapter(
            geometry_lookup=self._geometry_ok, http=self._client(lambda r: r)
        )
        with pytest.raises(DataGapError):
            adapter.search_scenes("no-field", date(2026, 6, 1), date(2026, 6, 21))

    def test_search_and_fetch_scene(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "token" in str(request.url):
                return httpx.Response(200, json=TOKEN_BODY)
            assert "Products" in str(request.url)
            assert "cloudCover" in str(request.url)  # фильтр облачности в OData
            return httpx.Response(200, json=_catalog_body("2026-06-10", 8.0))

        def raster_loader(scene_ref: str, band: str):
            # растр 4×4 в EPSG:4326 поверх контура поля (без перепроецирования)
            transform = Affine(0.0025, 0.0, 40.0, 0.0, -0.0025, 51.01)
            data = np.full((4, 4), 0.5 if band == "B08" else 0.1, dtype="float32")
            return data, transform, "EPSG:4326"

        adapter = self._adapter(handler, raster_loader=raster_loader)
        metas = adapter.search_scenes("ok", date(2026, 6, 1), date(2026, 6, 21))
        assert [m.scene_ref for m in metas] == [SCENE_ID]
        assert metas[0].captured_at == date(2026, 6, 10) and metas[0].cloud_pct == 8.0

        scenes = adapter.fetch_scenes("ok", "ndvi", date(2026, 6, 1), date(2026, 6, 21))
        assert len(scenes) == 1
        scene = scenes[0]
        assert scene.captured_at == date(2026, 6, 10)
        assert scene.cloud_pct == 8.0
        assert scene.cloud_method == "scene_metadata"
        assert scene.trust == 0.92
        expected = round((0.5 - 0.1) / (0.5 + 0.1), 3)
        assert (scene.index_avg, scene.index_min, scene.index_max) == (
            expected,
            expected,
            expected,
        )

    def test_catalog_5xx_is_unavailable(self):
        adapter = self._adapter(
            lambda request: (
                httpx.Response(200, json=TOKEN_BODY)
                if "token" in str(request.url)
                else httpx.Response(503)
            )
        )
        with pytest.raises(SourceUnavailableError):
            adapter.search_scenes("ok", date(2026, 6, 1), date(2026, 6, 21))


def _raise():
    raise AssertionError("raster_loader не должен вызываться")


# =============================================================================
# Ingest: дедупликация, облачность, событие шины, журнал
# =============================================================================


class TestIngest:
    @pytest.fixture(autouse=True)
    def _bus_isolated(self):
        events.reset_bus()
        yield
        events.reset_bus()

    @staticmethod
    def _ingest(db, adapter=None, **kwargs) -> SatelliteIngest:
        return SatelliteIngest(
            adapter or FakeSentinel2Adapter(),
            indices=("ndvi",),
            lookback_days=21,
            retry_attempts=1,
            **kwargs,
        )

    def test_pipeline_saves_scenes_publishes_and_journals(self, db_session):
        field_id = make_field(db_session)
        make_satellite_source(db_session)
        collected: list[events.Event] = []
        events.subscribe(SCENE_INGESTED_EVENT, collected.append)

        report = self._ingest(db_session).ingest_window(db_session)
        assert report.ok > 0 and not report.failed and report.status == "ok"
        rows = db_session.scalars(select(SatelliteScene)).all()
        assert len(rows) == report.ok
        assert {r.field_id for r in rows} == {field_id}
        assert all(r.index == "ndvi" and r.cloud_pct <= 20 for r in rows)
        # событие шины — по одной на новую сцену (ADR-01, 05 §2)
        assert len(collected) == report.ok
        assert all(e.event_type == SCENE_INGESTED_EVENT for e in collected)
        assert all(e.payload["field_id"] == str(field_id) for e in collected)
        # журнал адаптера и ref_source (API-28)
        entry = db_session.scalar(select(SourceJournalEntry))
        assert entry is not None and entry.source_id == SATELLITE_SOURCE_ID
        assert entry.status == "ok" and entry.reason is None
        source = db_session.scalar(select(Source).where(Source.source_id == SATELLITE_SOURCE_ID))
        assert source.last_status == "ok" and source.last_success_at is not None

    def test_second_run_deduplicates_without_new_events(self, db_session):
        make_field(db_session)
        make_satellite_source(db_session)
        collected: list[events.Event] = []
        events.subscribe(SCENE_INGESTED_EVENT, collected.append)

        first = self._ingest(db_session).ingest_window(db_session)
        collected.clear()
        second = self._ingest(db_session).ingest_window(db_session)
        assert second.ok == first.ok
        assert db_session.scalar(select(SatelliteScene.id).limit(1)) is not None
        total = len(db_session.scalars(select(SatelliteScene)).all())
        assert total == first.ok  # UQ(field_id, index, captured_at, source_id)
        assert collected == []  # новые сцены не появились — событий нет

    def test_cloudy_scene_above_60_not_saved(self, db_session):
        make_field(db_session)
        make_satellite_source(db_session)
        report = self._ingest(
            db_session, adapter=FakeSentinel2Adapter(forced_cloud_pct=70.0)
        ).ingest_window(db_session)
        assert report.ok == 0
        assert db_session.scalars(select(SatelliteScene)).first() is None
        assert report.status == "degraded"

    def test_scene_20_to_60_saved_with_lowered_threshold(self, db_session):
        make_field(db_session)
        make_satellite_source(db_session)
        report = self._ingest(
            db_session,
            adapter=FakeSentinel2Adapter(forced_cloud_pct=45.0),
            max_cloud_pct=50,
        ).ingest_window(db_session)
        assert report.ok > 0
        rows = db_session.scalars(select(SatelliteScene)).all()
        assert all(r.cloud_pct == 45.0 and float(r.trust) == 0.55 for r in rows)

    def test_db_check_rejects_cloud_above_60(self, db_session):
        field_id = make_field(db_session)
        make_satellite_source(db_session)
        db_session.add(
            SatelliteScene(
                field_id=field_id,
                index="ndvi",
                captured_at=date(2026, 6, 1),
                resolution_m=10,
                index_avg=0.5,
                index_min=0.4,
                index_max=0.6,
                cloud_pct=70.0,
                source_id=SATELLITE_SOURCE_ID,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_contour_lookup(self, db_session):
        field_id = make_field(db_session)
        lookup = contour_lookup_factory(db_session)
        contour = lookup(str(field_id))
        assert contour["type"] == "Polygon"
        assert lookup(str(uuid.uuid4())) is None


# =============================================================================
# API-06: таймлайн NDVI
# =============================================================================


class TestApi06:
    @pytest.fixture(autouse=True)
    def _setup(self, client, db_session):
        self.client = client
        self.db = db_session
        make_user(db_session, login="sat-agro", role=UserRole.AGRONOMIST, password="pw-sat")
        make_user(
            db_session, login="sat-mach", role=UserRole.MACHINE_OPERATOR, password="pw-sat2"
        )
        self.auth = auth_header(client, "sat-agro", "pw-sat")
        self.field_id = make_field(db_session)
        make_satellite_source(db_session)
        SatelliteIngest(
            FakeSentinel2Adapter(), indices=("ndvi",), lookback_days=21, retry_attempts=1
        ).ingest_window(db_session)
        yield

    def test_timeline_ndvi(self):
        resp = self.client.get(f"{API}/fields/{self.field_id}/ndvi", headers=self.auth)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"items", "total", "limit", "offset", "index", "meta"}
        assert body["index"] == "ndvi" and body["total"] == len(body["items"]) > 0
        dates = [item["captured_at"] for item in body["items"]]
        assert dates == sorted(dates, reverse=True)
        item = body["items"][0]
        assert {"scene_id", "index_avg", "index_min", "index_max", "cloud_pct",
                "composite_days", "trust"} <= set(item)
        assert body["meta"]["source_id"] == SATELLITE_SOURCE_ID
        assert body["meta"]["status"] == "ok"

    def test_date_filters(self):
        scenes = self.db.scalars(select(SatelliteScene)).all()
        middle = sorted(s.captured_at for s in scenes)[len(scenes) // 2]
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi",
            params={"date_from": middle.isoformat()},
            headers=self.auth,
        )
        assert resp.status_code == 200
        assert all(item["captured_at"] >= middle.isoformat() for item in resp.json()["items"])
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi",
            params={"date_to": middle.isoformat()},
            headers=self.auth,
        )
        assert all(item["captured_at"] <= middle.isoformat() for item in resp.json()["items"])

    def test_index_filter_and_unknown(self):
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi",
            params={"index": "ndre"},
            headers=self.auth,
        )
        assert resp.status_code == 200 and resp.json()["total"] == 0
        resp = self.client.get(
            f"{API}/fields/{self.field_id}/ndvi",
            params={"index": "evi"},
            headers=self.auth,
        )
        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "unknown_index"

    def test_unknown_field_404(self):
        resp = self.client.get(f"{API}/fields/{uuid.uuid4()}/ndvi", headers=self.auth)
        assert resp.status_code == 404 and resp.json()["code"] == "field_not_found"

    def test_rbac_mechanizer_forbidden(self):
        auth = auth_header(self.client, "sat-mach", "pw-sat2")
        resp = self.client.get(f"{API}/fields/{self.field_id}/ndvi", headers=auth)
        assert resp.status_code == 403


def test_scheduler_disabled_by_default():
    assert create_satellite_scheduler(Settings(satellite_enabled=False)) is None
