"""Тесты зон дифференцированного внесения VRA (v3-04, #94, docs/06 §7.4).

Проверяется по синтетическим COG (b04/b08 10 м UTM + scl 20 м, как в
test_sentinel_ndvi): три полосы развития NDVI → 3 зоны с подписями и
множителями дозы 1.15/1.0/0.85, сумма площадей зон ≈ площади поля;
dose_kg_ha от последнего approved fertilizing-плана (kg_ha; l_ha → null).
v1.7 (#121): ворота однородности удалены — зоны строятся ВСЕГДА при чистых
сценах, в ответе метрики iqr_ndvi + spatial_spread (p90−p10 средних ячеек
сетки ~20×20); поле без сцен → 422 vra_no_season_data; 404/403; map.png
(PNG-магия, X-Field-Bbox, X-Image-Size, Cache-Control 24 ч); кэш 24 ч по
(field_id, дата сцены) — повторный запрос не пересчитывает. Наружу тесты
не ходят: COG — локальные файлы; БД — DATABASE_URL_TEST; кэш — подменный
FakeRedis.
"""

import json
import uuid
from datetime import date

import numpy as np
import pytest
from app.models.agronomy import AgroInput, CropCycle, TreatmentPlan
from app.models.auth import UserRole
from app.models.integrations import SatelliteCatalogItem, SatelliteScene
from app.models.references import Culture
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_satellite_source

CRS = "EPSG:32637"  # UTM 37N — как в test_sentinel_ndvi
X0, Y1 = 300000.0, 5762000.0  # левый-верх сетки, 200×200 пикселей по 10 м
N = 200
SCENE_DATE = date(2026, 10, 31)
SATELLITE_SOURCE_ID = "satellite.sentinel2-l2a"

# NDVI полос при nir ≈ 3000: red 1600/800/300 → ~0.30 / ~0.58 / ~0.82
BAND_ROWS = (67, 134)  # границы трёх горизонтальных полос


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


def make_vra_rasters(tmp_path, red, *, seed=7) -> dict:
    """Синтетическая сцена: b04/b08 (10 м, 200×200), scl (20 м, растительность).

    NIR с небольшим шумом — чтобы перцентили не попадали точно на ступеньки
    полос и все три зоны были непустыми.
    """
    rng = np.random.default_rng(seed)
    nir = rng.uniform(2900, 3100, (N, N)).astype("uint16")
    tmp_path.mkdir(parents=True, exist_ok=True)  # подпапки сцен
    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y1)
    b04 = _write_tif(tmp_path / "b04.tif", red.astype("uint16"), t10, CRS)
    b08 = _write_tif(tmp_path / "b08.tif", nir, t10, CRS)
    scl = _write_tif(tmp_path / "scl.tif", np.full((N // 2, N), 4, "uint8"), t20, CRS)
    return {"b04": b04, "b08": b08, "scl": scl}


def striped_red() -> np.ndarray:
    """Три горизонтальные полосы 1600/800/300 → три уровня NDVI."""
    rows = np.arange(N)[:, None]
    stripes = np.where(rows < BAND_ROWS[0], 1600, np.where(rows < BAND_ROWS[1], 800, 300))
    return np.broadcast_to(stripes, (N, N)).copy()


def wgs_box(xmin, ymin, xmax, ymax) -> dict:
    return transform_geom(CRS, "EPSG:4326", mapping(box(xmin, ymin, xmax, ymax)))


def make_field(db: Session, name="Вра-1") -> uuid.UUID:
    """Поле 1800×1800 м (324 га) внутри плашки, покрывает все три полосы."""
    field_id = uuid.uuid4()
    geo = json.dumps(wgs_box(300100, 5760100, 301900, 5761900))
    db.execute(
        db_text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, 'active')"
        ),
        {"id": field_id, "name": name, "geo": geo},
    )
    db.commit()
    return field_id


def make_scene(
    db: Session,
    field_id: uuid.UUID,
    assets: dict,
    *,
    date_: date = SCENE_DATE,
    index_avg: float = 0.58,
) -> None:
    """Чистая NDVI-сцена + строка каталога с ассетами COG этой даты."""
    db.add(
        SatelliteScene(
            field_id=field_id,
            index="ndvi",
            captured_at=date_,
            resolution_m=10,
            index_min=round(index_avg - 0.2, 3),
            index_avg=index_avg,
            index_max=round(min(1.0, index_avg + 0.1), 3),
            cloud_pct=5.0,
            composite_days=1,
            source_id=SATELLITE_SOURCE_ID,
            trust=0.95,
        )
    )
    db.add(
        SatelliteCatalogItem(
            item_id=f"S2A_37UFT_{date_:%Y%m%d}",
            field_id=field_id,
            captured_at=date_,
            tile_id="37UFT",
            cloud_pct=5.0,
            assets=assets,
            status="processed",
        )
    )
    db.commit()


def make_approved_fertilizing(db: Session, field_id: uuid.UUID, unit: str, dose: float = 100.0):
    """Approved план внесения удобрения в цикле поля (сезон 2026)."""
    culture = Culture(name="Пшеница VRA", kind="main", forage_flag=False, crop_group="зерновые")
    db.add(culture)
    db.flush()
    cycle = CropCycle(field_id=field_id, year=2026, culture_id=culture.id, kind="main")
    db.add(cycle)
    db.flush()
    input_ = AgroInput(
        kind="fertilizer" if unit == "kg_ha" else "ppp",
        name=f"Препарат {unit}",
        unit=unit,
        **({"waiting_days": None} if unit == "kg_ha" else {"waiting_days": 5}),
    )
    db.add(input_)
    db.flush()
    plan = TreatmentPlan(
        cycle_id=cycle.id,
        input_id=input_.id,
        dose=dose,
        operation_type="fertilizing",
        planned_date=date(2026, 5, 1),
        status="approved",
    )
    db.add(plan)
    db.commit()


@pytest.fixture()
def vra_world(db_session, tmp_path):
    """Поле + чистая сцена по гетерогенному растру (3 полосы развития)."""
    make_satellite_source(db_session)
    field_id = make_field(db_session)
    make_scene(db_session, field_id, make_vra_rasters(tmp_path, striped_red()))
    return field_id


@pytest.fixture()
def agro_headers(client, db_session) -> dict:
    make_user(db_session, login="vra-agro", role=UserRole.AGRONOMIST, password="pw-vra")
    return auth_header(client, "vra-agro", "pw-vra")


class TestVraZones:
    def test_three_zones_and_areas(self, client, vra_world, agro_headers):
        """3 зоны по полосам: подписи, множители 1.15/1.0/0.85, NDVI растёт,
        сумма площадей ≈ площади поля; без approved-плана дозы null."""
        resp = client.get(f"/api/v1/fields/{vra_world}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["field_id"] == str(vra_world)
        assert body["date"] == SCENE_DATE.isoformat()
        assert body["iqr_ndvi"] >= 0.12
        assert body["base_dose_kg_ha"] is None
        assert body["map_png_url"].endswith(f"/fields/{vra_world}/vra/map.png")

        zones = body["zones"]
        assert len(zones) == 3
        assert [z["zone"] for z in zones] == [1, 2, 3]
        assert [z["label"] for z in zones] == [
            "пониженное развитие",
            "среднее развитие",
            "повышенное развитие",
        ]
        assert [z["dose_factor"] for z in zones] == [1.15, 1.0, 0.85]
        assert [z["dose_kg_ha"] for z in zones] == [None, None, None]
        # средние NDVI зон соответствуют полосам растра (~0.30/0.58/0.82)
        assert 0.25 < zones[0]["ndvi_mean"] < 0.38 < zones[1]["ndvi_mean"] < 0.65
        assert zones[1]["ndvi_mean"] < 0.75 < zones[2]["ndvi_mean"] < 0.9
        # поле 1800×1800 м = 324 га; зоны делят всю полезную площадь
        assert abs(sum(z["area_ga"] for z in zones) - 324.0) < 3.0

    def test_dose_from_approved_fertilizing(self, client, db_session, vra_world, agro_headers):
        """База — последний approved fertilizing kg_ha: дозы 115/100/85."""
        make_approved_fertilizing(db_session, vra_world, "kg_ha", dose=100.0)
        resp = client.get(f"/api/v1/fields/{vra_world}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["base_dose_kg_ha"] == 100.0
        assert [z["dose_kg_ha"] for z in body["zones"]] == [115.0, 100.0, 85.0]

    def test_dose_null_for_liquid_unit(self, client, db_session, vra_world, agro_headers):
        """План с единицей l_ha к кг/га не сводится — дозы null."""
        make_approved_fertilizing(db_session, vra_world, "l_ha", dose=2.0)
        resp = client.get(f"/api/v1/fields/{vra_world}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["base_dose_kg_ha"] is None
        assert all(z["dose_kg_ha"] is None for z in body["zones"])

    def test_uniform_field_zones_always(self, client, db_session, tmp_path, agro_headers):
        """v1.7: однородное поле (IQR < 0.12) — зоны всё равно строятся.

        Все пиксели в узком диапазоне: зоны почти неотличимы по ndvi_mean,
        iqr_ndvi < 0.12 и spatial_spread < 0.06 — дифвнесение не обосновано,
        но результаты «записаны в дифвнесения» (ворота удалены, #121).
        """
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Ровное-1")
        make_scene(db_session, field_id, make_vra_rasters(tmp_path, np.full((N, N), 800), seed=11))
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["iqr_ndvi"] < 0.12
        assert body["spatial_spread"] < 0.06
        zones = body["zones"]
        assert zones, "однородное поле тоже получает зоны (v1.7)"
        means = [z["ndvi_mean"] for z in zones]
        assert max(means) - min(means) < 0.05  # различия внутри шума NIR
        # поле 1800×1800 м = 324 га — зоны делят всю полезную площадь
        assert abs(sum(z["area_ga"] for z in zones) - 324.0) < 3.0

    def test_field_without_scenes_422(self, client, db_session, agro_headers):
        field_id = make_field(db_session, name="Без сцен")
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "vra_no_season_data"

    def test_unknown_field_404(self, client, agro_headers):
        resp = client.get(f"/api/v1/fields/{uuid.uuid4()}/vra", headers=agro_headers)
        assert resp.status_code == 404

    def test_machine_operator_403(self, client, db_session, vra_world):
        """Группа satellite (07-ux §5): машинисту-оператору зоны недоступны."""
        make_user(db_session, login="vra-op", role=UserRole.MACHINE_OPERATOR, password="pw-op")
        headers = auth_header(client, "vra-op", "pw-op")
        resp = client.get(f"/api/v1/fields/{vra_world}/vra", headers=headers)
        assert resp.status_code == 403


class TestVraMap:
    def test_png_headers_and_size(self, client, vra_world, agro_headers):
        """PNG зон: 3 оттенка, геопривязка X-Field-Bbox/X-Image-Size (§7.3)."""
        from rasterio.io import MemoryFile

        resp = client.get(f"/api/v1/fields/{vra_world}/vra/map.png", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/png"
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert "max-age=86400" in resp.headers["cache-control"]

        bbox = [float(v) for v in resp.headers["X-Field-Bbox"].split(",")]
        assert len(bbox) == 4 and bbox[0] < bbox[2] and bbox[1] < bbox[3]
        # окно поля в UTM (зона 37N, запад центрального меридиана 39°): ~36° с.ш. 52°
        assert 35.5 < bbox[0] < 37.0 and 51.0 < bbox[1] < 53.0

        width, _, height = resp.headers["X-Image-Size"].partition("x")
        with MemoryFile(resp.content) as mem, mem.open() as src:
            assert (src.width, src.height) == (int(width), int(height))
        # окно 1800 м при пикселе 10 м: ~180 px (±1 на округление границ контура
        # при перепроекции в UTM), прореживание до 1024 не срабатывает
        assert 178 < int(width) <= 184 and 178 < int(height) <= 184

    def test_machine_operator_403(self, client, db_session, vra_world):
        make_user(db_session, login="vra-op2", role=UserRole.MACHINE_OPERATOR, password="pw-op")
        headers = auth_header(client, "vra-op2", "pw-op")
        resp = client.get(f"/api/v1/fields/{vra_world}/vra/map.png", headers=headers)
        assert resp.status_code == 403


class FakeRedis:
    """Подменный клиент кэша: get/setex над словарём (значения уже JSON)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


class TestVraCache:
    def test_second_request_served_from_cache(self, client, vra_world, agro_headers, monkeypatch):
        """Кэш 24 ч по (field_id, дата сцены): повторный запрос без пересчёта."""
        import app.core.cache as cache_mod
        from app.modules.satellite import vra

        fake = FakeRedis()
        monkeypatch.setattr(cache_mod, "_redis", fake)

        calls = {"n": 0}
        real_analyze = vra._analyze_scene

        def counted(db, field, scene):
            calls["n"] += 1
            return real_analyze(db, field, scene)

        monkeypatch.setattr(vra, "_analyze_scene", counted)

        first = client.get(f"/api/v1/fields/{vra_world}/vra", headers=agro_headers)
        assert first.status_code == 200, first.text
        assert calls["n"] == 1
        assert f"vra:{vra_world}:{SCENE_DATE.isoformat()}" in fake.store
        assert f"vra:peak:{vra_world}" in fake.store  # указатель для insights

        second = client.get(f"/api/v1/fields/{vra_world}/vra", headers=agro_headers)
        assert second.status_code == 200, second.text
        assert calls["n"] == 1  # пересчёта не было
        assert second.json() == first.json()

        map2 = client.get(f"/api/v1/fields/{vra_world}/vra/map.png", headers=agro_headers)
        assert map2.status_code == 200
        assert calls["n"] == 1


class TestVraPeaks:
    """Зоны по пиковым сценам сезона (§7.4 v1.4, #106): топ-3 по index_avg,
    подтверждение IQR ≥ 0.12 на ≥2 из 3, зоны — по максимальному index_avg."""

    def add_scene(self, db, tmp_path, field_id, sub, day, red, index_avg):
        assets = make_vra_rasters(tmp_path / sub, red, seed=abs(hash(sub)) % 1000)
        make_scene(db, field_id, assets, date_=day, index_avg=index_avg)

    def test_zones_from_peak_not_latest(self, client, db_session, tmp_path, agro_headers):
        """Пик вегетации 06-20/06-25 разнороден, ноябрьская сцена однородна:
        зоны по 06-20 (макс index_avg), последняя сцена не используется."""
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Пик-1")
        self.add_scene(db_session, tmp_path, field_id, "s1", date(2026, 6, 25), striped_red(), 0.80)
        self.add_scene(
            db_session,
            tmp_path,
            field_id,
            "s2",
            date(2026, 11, 5),
            np.full((N, N), 800),
            0.30,  # последняя по дате — однородная
        )
        self.add_scene(db_session, tmp_path, field_id, "s3", date(2026, 6, 20), striped_red(), 0.82)
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["date"] == "2026-06-20"  # макс index_avg среди подтверждённых
        assert body["peak_dates"] == ["2026-06-20", "2026-06-25", "2026-11-05"]
        assert body["iqr_ndvi"] >= 0.12
        assert len(body["zones"]) == 3

    def test_single_heterogeneous_peak_zones(self, client, db_session, tmp_path, agro_headers):
        """v1.7: IQR ≥ 0.12 только на 1 из 3 пиковых сцен — зоны всё равно
        строятся, по сцене с максимальным index_avg (ворота удалены)."""
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Один пик")
        self.add_scene(db_session, tmp_path, field_id, "s1", date(2026, 6, 20), striped_red(), 0.82)
        self.add_scene(
            db_session,
            tmp_path,
            field_id,
            "s2",
            date(2026, 6, 25),
            np.full((N, N), 800),
            0.78,
        )
        self.add_scene(
            db_session,
            tmp_path,
            field_id,
            "s3",
            date(2026, 7, 2),
            np.full((N, N), 900),
            0.74,
        )
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["date"] == "2026-06-20"  # макс index_avg
        assert body["iqr_ndvi"] >= 0.12
        assert len(body["zones"]) == 3

    def test_two_scenes_one_peak_enough(self, client, db_session, tmp_path, agro_headers):
        """<3 сцен: подтверждение на всех, кроме максимум одной — 1 из 2 достаточно."""
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Две сцены")
        self.add_scene(db_session, tmp_path, field_id, "s1", date(2026, 6, 20), striped_red(), 0.82)
        self.add_scene(
            db_session,
            tmp_path,
            field_id,
            "s2",
            date(2026, 6, 25),
            np.full((N, N), 800),
            0.78,
        )
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["date"] == "2026-06-20"
        assert body["peak_dates"] == ["2026-06-20", "2026-06-25"]

    def test_two_uniform_scenes_zones(self, client, db_session, tmp_path, agro_headers):
        """v1.7: однородный сезон из 2 сцен — зоны строятся, без 422."""
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Ровный сезон")
        for sub, day in (("s1", date(2026, 6, 20)), ("s2", date(2026, 6, 25))):
            self.add_scene(db_session, tmp_path, field_id, sub, day, np.full((N, N), 800), 0.7)
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["iqr_ndvi"] < 0.12
        assert body["spatial_spread"] < 0.06
        assert body["zones"]

    def test_peak_cache_full_for_all_scenes(
        self, client, db_session, tmp_path, agro_headers, monkeypatch
    ):
        """Кэш v1.7: полный расчёт (зоны+spread) — по дате КАЖДОЙ пиковой
        сцены, ключей vra:iqr:* больше нет; повторный запрос не перечитывает
        COG; указатель vra:peak хранит метрики всех пиков."""
        import json as json_mod

        import app.core.cache as cache_mod
        from app.modules.satellite import vra

        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Кэш пиков")
        self.add_scene(db_session, tmp_path, field_id, "s1", date(2026, 6, 20), striped_red(), 0.82)
        self.add_scene(db_session, tmp_path, field_id, "s2", date(2026, 6, 25), striped_red(), 0.80)
        self.add_scene(
            db_session,
            tmp_path,
            field_id,
            "s3",
            date(2026, 11, 5),
            np.full((N, N), 800),
            0.30,
        )

        fake = FakeRedis()
        monkeypatch.setattr(cache_mod, "_redis", fake)
        calls = {"n": 0}
        real_analyze = vra._analyze_scene

        def counted(db, field, scene):
            calls["n"] += 1
            return real_analyze(db, field, scene)

        monkeypatch.setattr(vra, "_analyze_scene", counted)

        first = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert first.status_code == 200, first.text
        assert calls["n"] == 3  # по одному чтению COG на каждую пиковую сцену
        for day in ("2026-06-20", "2026-06-25", "2026-11-05"):
            key = f"vra:{field_id}:{day}"
            assert key in fake.store  # полный расчёт для всех пиков (v1.7)
        assert not any(k.startswith(f"vra:iqr:{field_id}") for k in fake.store)  # путь удалён

        pointer = json_mod.loads(fake.store[f"vra:peak:{field_id}"])
        assert [p["date"] for p in pointer["peaks"]] == [
            "2026-06-20",
            "2026-06-25",
            "2026-11-05",
        ]
        assert all(
            {"iqr_ndvi", "spatial_spread"} <= set(p) for p in pointer["peaks"]
        )  # детектор §8.2 v1.7

        second = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert second.status_code == 200, second.text
        assert calls["n"] == 3  # всё из кэша
        assert second.json() == first.json()


def spotty_red() -> np.ndarray:
    """Фон red≈340 (NDVI ≈ 0.80) с 12 пятнами 18×18 px (~3.2 га каждое,
    red≈650 → NDVI ≈ 0.65): суммарно ~12 % пикселей — пиксельный IQR их не
    видит (< p25), а средние ячеек сетки 20×20 — видят: пятна накрывают
    ~15 % ячеек, p10 попадает в пятна (§7.4 v1.7, поле 50 #364)."""
    red = np.full((N, N), 340)
    for y in (35, 85, 135):
        for x in (30, 75, 120, 160):
            red[y : y + 18, x : x + 18] = 650
    return red


class TestVraSpatialSpread:
    """Пространственный критерий неоднородности (§7.4 v1.7, #121): мелкие
    пятна ~3 га при узком пиксельном IQR — spatial_spread их видит."""

    def test_spots_seen_by_spread_not_iqr(self, client, db_session, tmp_path, agro_headers):
        make_satellite_source(db_session)
        field_id = make_field(db_session, name="Пятна-1")
        assets = make_vra_rasters(tmp_path, spotty_red(), seed=13)
        make_scene(db_session, field_id, assets, index_avg=0.80)
        resp = client.get(f"/api/v1/fields/{field_id}/vra", headers=agro_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["spatial_spread"] >= 0.06  # пятна видны пространственно
        assert body["iqr_ndvi"] < 0.12  # ...но невидимы пиксельным IQR
        low = [z for z in body["zones"] if z["ndvi_mean"] < 0.70]
        assert low, "пятна образуют зону пониженного развития"
        # 12 пятен × 18×18 px из ~180×180 полезных ≈ 12 % от 324 га ≈ 39 га
        assert 30.0 < low[0]["area_ga"] < 48.0

    def test_detector_spatial_criterion(self):
        """Детектор §8.2 v1.7: spread ≥ 0.06 на 2 из 3 пиков при IQR < 0.12."""
        from app.ai_assistant.detectors import detect_vra_heterogeneity

        peaks = [
            (date(2026, 6, 20), 0.031, 0.09),
            (date(2026, 6, 25), 0.028, 0.075),
            (date(2026, 7, 2), 0.042, 0.01),
        ]
        item = detect_vra_heterogeneity(peaks)
        assert item is not None
        assert item.code == "vra_heterogeneity"
        assert "однородно" not in item.detail
        assert "дифвнесени" in item.detail
        assert len(item.data_refs) == 3
        assert {"iqr_ndvi", "spatial_spread"} <= set(item.data_refs[0])

    def test_detector_iqr_regression(self):
        """Регресс: прежний критерий IQR ≥ 0.12 на 2 из 3 пиков — срабатывает."""
        from app.ai_assistant.detectors import detect_vra_heterogeneity

        peaks = [
            (date(2026, 6, 20), 0.15, 0.0),
            (date(2026, 6, 25), 0.13, 0.0),
            (date(2026, 7, 2), 0.05, 0.0),
        ]
        assert detect_vra_heterogeneity(peaks) is not None

    def test_detector_uniform_silent(self):
        """Однородные пики (обе метрики ниже порогов) — детектор молчит."""
        from app.ai_assistant.detectors import detect_vra_heterogeneity

        peaks = [
            (date(2026, 6, 20), 0.03, 0.01),
            (date(2026, 6, 25), 0.04, 0.02),
            (date(2026, 7, 2), 0.05, 0.0),
        ]
        assert detect_vra_heterogeneity(peaks) is None

    def test_detector_single_noisy_scene_not_enough(self):
        """Шум одной сцены (порог breached только на 1 из 3) — молчит."""
        from app.ai_assistant.detectors import detect_vra_heterogeneity

        peaks = [
            (date(2026, 6, 20), 0.03, 0.01),
            (date(2026, 6, 25), 0.20, 0.0),  # выброс одной сцены
            (date(2026, 7, 2), 0.04, 0.02),
        ]
        assert detect_vra_heterogeneity(peaks) is None
