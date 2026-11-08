"""Тесты детекторов §7.7 #129: drought по NDMI, winterkill по NDSI, VRA по NDRE.

drought: NDMI-сигнал (ndmi_mean < −0.1 при NDVI > 0.35) дополняет правило
осадков/GDD, detail указывает сработавший сигнал. winterkill: доля зимних
сцен (дек–мар) «NDSI < 0.4 ∧ NDVI < 0.2», только для озимых, info < 30 % ≤
warn. VRA: /vra?index=ndre — зоны по B08/B05, отдельный кэш-ключ, указатель
детектора vra:peak не перезаписывается. БД — DATABASE_URL_TEST.
"""

from datetime import date

import numpy as np
import pytest
from app.ai_assistant import detectors
from app.models.agronomy import CropCycle
from app.models.integrations import SatelliteScene
from app.models.references import Culture
from rasterio.transform import Affine
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_satellite import make_satellite_source
from tests.test_vra import CRS, N, X0, Y1, _write_tif
from tests.test_vra import make_field as make_vra_field

SATELLITE_SOURCE_ID = "satellite.sentinel2-l2a"
API = "/api/v1"


# -- drought: NDMI-сигнал (§7.7 #129) ----------------------------------------


class TestDroughtNdmi:
    def test_precip_signal_only(self) -> None:
        """Прежнее правило (осадки ∧ GDD) работает без NDMI-данных."""
        item = detectors.detect_drought(5.0, 160.0, 10, date(2026, 7, 1))
        assert item is not None and item.code == "drought"
        assert "садков за 14 суток" in item.detail  # Осадков/осадков
        assert "NDMI" not in item.detail

    def test_ndmi_signal_only(self) -> None:
        """NDMI-стресс при живой вегетации — засуха и без сухого окна осадков."""
        item = detectors.detect_drought(
            40.0, 60.0, 10, date(2026, 7, 1), ndmi_mean=-0.21, ndvi_mean=0.52
        )
        assert item is not None
        assert "NDMI -0.21" in item.detail and "садков за 14 суток" not in item.detail

    def test_both_signals(self) -> None:
        item = detectors.detect_drought(
            3.0, 150.0, 10, date(2026, 7, 1), ndmi_mean=-0.15, ndvi_mean=0.6
        )
        assert item is not None
        assert "садков за 14 суток" in item.detail and "NDMI -0.15" in item.detail
        assert any(r.get("ndmi_mean") == -0.15 for r in item.data_refs)

    def test_ndmi_requires_vegetation(self) -> None:
        """NDVI ≤ 0.35 — вегетации нет, NDMI-сигнал не подстава."""
        assert (
            detectors.detect_drought(
                40.0, 60.0, 10, date(2026, 7, 1), ndmi_mean=-0.3, ndvi_mean=0.2
            )
            is None
        )

    def test_no_signal_silent(self) -> None:
        assert (
            detectors.detect_drought(40.0, 60.0, 10, date(2026, 7, 1), ndmi_mean=0.1, ndvi_mean=0.6)
            is None
        )


# -- winterkill (§7.7 #129) --------------------------------------------------


class TestWinterkill:
    def test_warn_on_share(self) -> None:
        """≥ 30 % оголённых сцен — warn, detail с датами и значениями."""
        pairs = [
            (date(2027, 1, 5), 0.12, 0.1),  # оголено, без снега
            (date(2027, 1, 15), 0.15, 0.2),
            (date(2027, 2, 1), 0.55, 0.1),  # посев жив
            (date(2027, 2, 20), 0.18, 0.3),
        ]
        item = detectors.detect_winterkill(pairs, "Озимая пшеница")
        assert item is not None and item.code == "winterkill"
        assert item.severity == "warn"  # 3/4 = 75 %
        assert "75 % зимних сцен" in item.detail
        assert "2027-01-05 (NDVI 0.12, NDSI 0.10)" in item.detail

    def test_info_below_threshold(self) -> None:
        pairs = [
            (date(2027, 1, 5), 0.12, 0.1),  # оголено
            (date(2027, 1, 15), 0.55, 0.1),  # живой посев
            (date(2027, 2, 1), 0.6, 0.2),  # живой посев
        ]
        item = detectors.detect_winterkill(pairs, "Рожь озимая")
        assert item is not None and item.severity == "info"

    def test_snow_protects(self) -> None:
        """NDSI ≥ 0.4 — снег закрыл посев, оголением не считается."""
        pairs = [(date(2027, 1, 5), 0.12, 0.7), (date(2027, 2, 1), 0.14, 0.55)]
        assert detectors.detect_winterkill(pairs, "Озимая пшеница") is None

    def test_only_winter_cultures(self) -> None:
        pairs = [(date(2027, 1, 5), 0.12, 0.1)]
        assert detectors.detect_winterkill(pairs, "Гречиха") is None
        assert detectors.detect_winterkill(pairs, None) is None

    def test_no_pairs_silent(self) -> None:
        assert detectors.detect_winterkill([], "Озимая пшеница") is None


# -- данные: load_winter_pairs / latest_index_mean / build_items -------------


def _scene(db: Session, field_id, index: str, day: date, avg: float) -> None:
    db.add(
        SatelliteScene(
            field_id=field_id,
            index=index,
            captured_at=day,
            resolution_m=10,
            index_min=round(avg - 0.2, 3),
            index_avg=avg,
            index_max=round(avg + 0.2, 3),
            cloud_pct=5.0,
            composite_days=1,
            source_id=SATELLITE_SOURCE_ID,
            trust=0.95,
        )
    )


class TestWinterDataLoaders:
    def test_load_winter_pairs_window_and_join(self, db_session) -> None:
        """Окно декабрь Y−1 … март Y; пары только по датам с NDVI и NDSI."""
        make_satellite_source(db_session)
        field_id = make_vra_field(db_session)
        winter = [
            (date(2026, 12, 20), 0.15, 0.2),
            (date(2027, 1, 10), 0.5, 0.1),  # NDVI есть, NDSI нет
            (date(2027, 2, 5), 0.12, 0.3),
        ]
        for day, ndvi, ndsi in winter:
            _scene(db_session, field_id, "ndvi", day, ndvi)
            if day != date(2027, 1, 10):
                _scene(db_session, field_id, "ndsi", day, ndsi)
        # вне окна: апрель и декабрь следующей зимой
        _scene(db_session, field_id, "ndvi", date(2027, 4, 1), 0.3)
        _scene(db_session, field_id, "ndsi", date(2027, 4, 1), 0.3)
        _scene(db_session, field_id, "ndvi", date(2026, 11, 30), 0.2)
        _scene(db_session, field_id, "ndsi", date(2026, 11, 30), 0.2)
        db_session.commit()

        pairs = detectors.load_winter_pairs(db_session, field_id, 2027)
        assert pairs == [
            (date(2026, 12, 20), 0.15, 0.2),
            (date(2027, 2, 5), 0.12, 0.3),
        ]

    def test_latest_index_mean(self, db_session) -> None:
        make_satellite_source(db_session)
        field_id = make_vra_field(db_session)
        _scene(db_session, field_id, "ndmi", date(2026, 7, 1), -0.05)
        _scene(db_session, field_id, "ndmi", date(2026, 8, 1), -0.21)
        _scene(db_session, field_id, "ndvi", date(2026, 8, 1), 0.5)
        db_session.commit()
        assert detectors.latest_index_mean(db_session, field_id, "ndmi") == pytest.approx(-0.21)
        assert detectors.latest_index_mean(db_session, field_id, "ndre") is None


class TestBuildItemsDetectors:
    def test_drought_ndmi_in_items(self, db_session) -> None:
        """build_items: NDMI-сигнал без погодных данных даёт drought."""
        from app.ai_assistant.service import build_items

        make_satellite_source(db_session)
        field_id = make_vra_field(db_session)
        _scene(db_session, field_id, "ndvi", date(2026, 8, 1), 0.52)
        _scene(db_session, field_id, "ndmi", date(2026, 8, 1), -0.21)
        db_session.commit()
        codes = [i.code for i in build_items(db_session, field_id)]
        assert "drought" in codes

    def test_winterkill_in_items_for_winter_culture(self, db_session) -> None:
        """build_items: озимые + зимние сцены (дек Y−1 → сезон Y+1)."""
        from app.ai_assistant.service import build_items

        make_satellite_source(db_session)
        field_id = make_vra_field(db_session)
        culture = Culture(name="Озимая пшеница", kind="cereal", crop_group="cereal")
        db_session.add(culture)
        db_session.flush()
        db_session.add(CropCycle(field_id=field_id, year=2027, culture_id=culture.id, kind="main"))
        # последняя сцена — декабрь 2026 (месяц 12 → сезон 2027)
        _scene(db_session, field_id, "ndvi", date(2026, 12, 20), 0.13)
        _scene(db_session, field_id, "ndsi", date(2026, 12, 20), 0.15)
        db_session.commit()
        items = build_items(db_session, field_id)
        codes = [i.code for i in items]
        assert "winterkill" in codes
        winterkill = next(i for i in items if i.code == "winterkill")
        assert winterkill.severity == "warn"  # 1/1 сцен

    def test_winterkill_not_for_summer_culture(self, db_session) -> None:
        from app.ai_assistant.service import build_items

        make_satellite_source(db_session)
        field_id = make_vra_field(db_session)
        _scene(db_session, field_id, "ndvi", date(2026, 12, 20), 0.13)
        _scene(db_session, field_id, "ndsi", date(2026, 12, 20), 0.15)
        db_session.commit()
        assert "winterkill" not in [i.code for i in build_items(db_session, field_id)]


# -- VRA по NDRE (§7.7 #129) -------------------------------------------------


@pytest.fixture()
def ndre_world(tmp_path, db_session):
    """Поле + сцены ndre + каталог с ассетами b08/b05(20 м)/scl."""
    make_satellite_source(db_session)
    field_id = make_vra_field(db_session, name="Эндре-1")

    rng = np.random.default_rng(5)
    nir = rng.uniform(2900, 3100, (N, N)).astype("uint16")
    # три полосы rededge → три уровня NDRE
    rows = np.arange(N)[:, None]
    rededge_10 = np.where(rows < 67, 1600, np.where(rows < 134, 1100, 800))
    rededge_10 = np.broadcast_to(rededge_10, (N, N)).copy()
    # b05 — 20 м: каждое значение покрывает 2×2 пикселя 10-м сетки
    rededge_20 = rededge_10[::2, ::2]

    t10 = Affine(10.0, 0.0, X0, 0.0, -10.0, Y1)
    t20 = Affine(20.0, 0.0, X0, 0.0, -20.0, Y1)
    assets = {
        "b08": _write_tif(tmp_path / "b08.tif", nir, t10, CRS),
        "b05": _write_tif(tmp_path / "b05.tif", rededge_20.astype("uint16"), t20, CRS),
        "scl": _write_tif(tmp_path / "scl.tif", np.full((N // 2, N), 4, "uint8"), t20, CRS),
    }
    from app.models.integrations import SatelliteCatalogItem

    for day in (date(2026, 10, 20), date(2026, 10, 31)):
        _scene(db_session, field_id, "ndre", day, 0.4)
        db_session.add(
            SatelliteCatalogItem(
                item_id=f"S2_37UFT_NDRE_{day.isoformat()}",
                field_id=field_id,
                captured_at=day,
                tile_id="37UFT",
                cloud_pct=5.0,
                assets=assets,
                status="processed",
            )
        )
    db_session.commit()
    return field_id, assets


class TestVraNdre:
    def test_vra_zones_by_ndre(self, ndre_world, db_session) -> None:
        """Зоны по NDRE строятся: 3 зоны, метрики по NDRE-массиву."""
        from app.models.agronomy import Field
        from app.modules.satellite import vra

        field_obj = db_session.get(Field, ndre_world[0])
        result = vra.vra_zones(db_session, field_obj, index="ndre")
        assert len(result.zones) == 3
        means = [z.ndvi_mean for z in result.zones]  # средние NDRE зон
        assert means == sorted(means)
        # полосы: NDRE ≈ (3000−1600)/(4600) ≈ 0.30 … (3000−800)/3800 ≈ 0.58
        assert means[0] < means[2]
        assert 0.2 < means[0] < 0.7

    def test_vra_ndre_cache_key_separate(self, ndre_world, db_session) -> None:
        """Не-ndvi кэш — с суффиксом :ndre; vra:peak не пишется для ndre."""
        from app.core.cache import cache_get
        from app.models.agronomy import Field
        from app.modules.satellite import vra

        field_obj = db_session.get(Field, ndre_world[0])
        vra.vra_zones(db_session, field_obj, index="ndre")
        day = db_session.scalar(
            db_text(
                "SELECT max(captured_at) FROM dat_satellite_scene "
                "WHERE field_id = :fid AND \"index\" = 'ndre'"
            ),
            {"fid": ndre_world[0]},
        )
        assert cache_get(f"vra:{ndre_world[0]}:{day}:ndre") is not None
        assert cache_get(f"vra:{ndre_world[0]}:{day}") is None  # ndvi-ключ не занят
        assert cache_get(f"vra:peak:{ndre_world[0]}") is None  # указатель не писался

    def test_api_vra_index_ndre(self, client, ndre_world, db_session) -> None:
        """/vra?index=ndre: зоны по NDRE + поле index; 422 на чужой индекс."""
        from app.models.auth import UserRole

        make_user(db_session, login="ndre-agro", role=UserRole.AGRONOMIST, password="secret123")
        headers = auth_header(client, "ndre-agro", "secret123")
        resp = client.get(
            f"{API}/fields/{ndre_world[0]}/vra",
            params={"index": "ndre"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["index"] == "ndre" and len(body["zones"]) == 3

        bad = client.get(
            f"{API}/fields/{ndre_world[0]}/vra",
            params={"index": "bsi"},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["code"] == "unknown_index"
