"""Тесты выгрузки карты дифвнесения API-60 (§7.4, #125).

По синтетическому COG с тремя полосами NDVI (как test_vra.py): GeoJSON —
фича на зону с дозами; Shapefile-ZIP читается pyshp, атрибуты и части на
месте; ISOXML-ZIP содержит TASKDATA.XML с PFD/TRT/PDV DDI 1 и полигонами;
площади зон сходятся с API-52; 422 vra_no_base_dose без дозы; 422
unknown_export_format; ?year → 404 season_not_found. Наружу не ходим.
"""

import io
import json
import uuid
import zipfile
from xml.etree.ElementTree import fromstring

import numpy as np
import pytest
from app.models.auth import UserRole
from rasterio.transform import Affine
from shapely.geometry import shape
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_satellite_source
from tests.test_vra import (
    make_field,
    make_scene,
    make_vra_rasters,
    striped_red,
)

API = "/api/v1"


@pytest.fixture()
def vra_world(db_session, tmp_path):
    """Поле 324 га с полосатой сценой (3 зоны) + approved план 100 кг/га."""
    from tests.test_vra import make_approved_fertilizing

    make_satellite_source(db_session)
    field_id = make_field(db_session, name="Экспорт-1")
    make_scene(db_session, field_id, make_vra_rasters(tmp_path, striped_red()))
    make_approved_fertilizing(db_session, field_id, "kg_ha", dose=100.0)
    return field_id


@pytest.fixture()
def agro_headers(client, db_session) -> dict:
    make_user(db_session, login="exp-agro", role=UserRole.AGRONOMIST, password="pw-exp")
    return auth_header(client, "exp-agro", "pw-exp")


def _geojson(client, field_id, headers) -> dict:
    resp = client.get(
        f"{API}/fields/{field_id}/vra/export",
        params={"format": "geojson"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/geo+json")
    return json.loads(resp.text)


class TestGeojson:
    def test_features_doses_and_areas(self, client, vra_world, agro_headers):
        body = _geojson(client, vra_world, agro_headers)
        assert body["type"] == "FeatureCollection"
        feats = body["features"]
        assert len(feats) == 3
        assert [f["properties"]["zone"] for f in feats] == [1, 2, 3]
        # дозы от плана 100 кг/га: 115 / 100 / 85
        assert [f["properties"]["dose_kg_ha"] for f in feats] == [115.0, 100.0, 85.0]
        assert [f["properties"]["dose_factor"] for f in feats] == [1.15, 1.0, 0.85]
        # полигоны валидны и площади сходятся с API-52 (пиксельные area_ga)
        api52 = client.get(f"{API}/fields/{vra_world}/vra", headers=agro_headers).json()
        for feat, zone52 in zip(feats, api52["zones"], strict=True):
            geom = shape(feat["geometry"])
            assert geom.is_valid and geom.area > 0
            assert abs(feat["properties"]["area_ga"] - zone52["area_ga"]) < 0.01

    def test_explicit_base_dose_wins(self, client, vra_world, agro_headers):
        """base_dose_kg_ha=200 параметром перекрывает план 100 кг/га."""
        resp = client.get(
            f"{API}/fields/{vra_world}/vra/export",
            params={"format": "geojson", "base_dose_kg_ha": 200},
            headers=agro_headers,
        )
        assert resp.status_code == 200
        doses = [f["properties"]["dose_kg_ha"] for f in json.loads(resp.text)["features"]]
        assert doses == [230.0, 200.0, 170.0]


class TestShapefile:
    def test_zip_readable_by_pyshp(self, client, vra_world, agro_headers):
        import shapefile

        resp = client.get(
            f"{API}/fields/{vra_world}/vra/export",
            params={"format": "shp"},
            headers=agro_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            assert "vra_zones.shp" in names and "vra_zones.dbf" in names
            assert "vra_zones.prj" in names
            zf.extractall("/tmp/cda_shp_test")
        reader = shapefile.Reader("/tmp/cda_shp_test/vra_zones", encoding="utf-8")
        assert reader.shapeTypeName == "POLYGON"
        assert len(reader) == 3
        doses = [r[5] for r in reader.records()]
        assert doses == [115.0, 100.0, 85.0]
        # каждая часть (кольцо) замкнута и непуста
        for shp in reader.shapes():
            bounds = [*shp.parts, len(shp.points)]
            for start, end in zip(bounds, bounds[1:], strict=False):
                ring = shp.points[start:end]
                assert len(ring) >= 4 and ring[0] == ring[-1]


class TestIsoxml:
    def test_taskdata_xml_structure(self, client, vra_world, agro_headers):
        resp = client.get(
            f"{API}/fields/{vra_world}/vra/export",
            params={"format": "isoxml"},
            headers=agro_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert "TASKDATA.XML" in zf.namelist()
            xml = zf.read("TASKDATA.XML")
        root = fromstring(xml)
        assert root.tag == "ISO11783_TaskData"
        assert root.get("versionMajor") == "4"
        pfd = root.find("PFD")
        assert pfd is not None and "Экспорт-1" in pfd.get("A", "")
        trts = pfd.findall("TRT")
        assert len(trts) == 3
        # PDV DDI 1 (кг/га, шаг 0.01): 115/100/85 кг/га
        values = sorted(int(trt.find("PDV").get("E")) for trt in trts)
        assert values == [8500, 10000, 11500]
        # у каждой зоны полигон из точек в миллионных долях градуса
        for trt in trts:
            points = trt.findall(".//PNT")
            assert len(points) >= 4
            assert all(0 < int(p.get("D")) < 180_000_000 for p in points)


def _uniform_rasters(tmp_path) -> dict:
    """Полностью однородная сцена (NIR без шума): зоны 1 и 3 пусты —
    только зона 2 «среднее» (кластеризация при p30 == p70 == всем пикселям)."""
    from tests.test_vra import CRS, N, _write_tif

    tmp_path.mkdir(parents=True, exist_ok=True)
    t10 = Affine(10.0, 0.0, 300000.0, 0.0, -10.0, 5762000.0)
    t20 = Affine(20.0, 0.0, 300000.0, 0.0, -20.0, 5762000.0)
    red = _write_tif(tmp_path / "b04.tif", np.full((N, N), 800, "uint16"), t10, CRS)
    nir = _write_tif(tmp_path / "b08.tif", np.full((N, N), 3000, "uint16"), t10, CRS)
    scl = _write_tif(tmp_path / "scl.tif", np.full((N // 2, N), 4, "uint8"), t20, CRS)
    return {"b04": red, "b08": nir, "scl": scl}


class TestEmptyZoneAlignment:
    """Ревью #125: пустые зоны не должны сдвигать выравнивание зон и доз."""

    def test_uniform_field_single_zone_correct_dose(
        self, client, db_session, tmp_path, agro_headers
    ):
        """Однородное поле: единственная зона 2 (dose_factor 1.0, доза = базе).

        Наивный zip зон с тремя масками терял зону 2 (маска 1 пуста).
        """
        from tests.test_vra import make_approved_fertilizing

        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Однородное-эксп")
        make_scene(db_session, field_id, _uniform_rasters(tmp_path / "uni"))
        make_approved_fertilizing(db_session, field_id, "kg_ha", dose=100.0)

        resp = client.get(
            f"{API}/fields/{field_id}/vra/export",
            params={"format": "geojson"},
            headers=agro_headers,
        )
        assert resp.status_code == 200, resp.text
        feats = json.loads(resp.text)["features"]
        assert len(feats) == 1
        props = feats[0]["properties"]
        assert props["zone"] == 2
        assert props["dose_factor"] == 1.0
        assert props["dose_kg_ha"] == 100.0  # не сдвинутая доза зоны 3


class TestErrorsAndRbac:
    def test_no_base_dose_422(self, client, db_session, tmp_path, agro_headers):
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Без дозы")
        make_scene(db_session, field_id, make_vra_rasters(tmp_path, striped_red()))
        resp = client.get(
            f"{API}/fields/{field_id}/vra/export",
            params={"format": "geojson"},
            headers=agro_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "vra_no_base_dose"

    def test_unknown_format_422(self, client, vra_world, agro_headers):
        resp = client.get(
            f"{API}/fields/{vra_world}/vra/export", params={"format": "kml"}, headers=agro_headers
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "unknown_export_format"

    def test_year_without_season_404(self, client, vra_world, agro_headers):
        resp = client.get(
            f"{API}/fields/{vra_world}/vra/export",
            params={"format": "geojson", "year": 2024},
            headers=agro_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "season_not_found"

    def test_unknown_field_404(self, client, agro_headers):
        resp = client.get(
            f"{API}/fields/{uuid.uuid4()}/vra/export",
            params={"format": "geojson"},
            headers=agro_headers,
        )
        assert resp.status_code == 404

    def test_machine_operator_403(self, client, db_session, vra_world):
        make_user(db_session, login="exp-op", role=UserRole.MACHINE_OPERATOR, password="pw-op")
        headers = auth_header(client, "exp-op", "pw-op")
        resp = client.get(
            f"{API}/fields/{vra_world}/vra/export", params={"format": "geojson"}, headers=headers
        )
        assert resp.status_code == 403
