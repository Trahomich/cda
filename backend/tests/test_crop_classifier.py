"""Тесты классификатора культуры по NDVI-кривой (#155, разовый анализ).

Синтетические кривые: озимая (осенний подъём + апрельский старт), яровая
(один пик май–июль без осени), травы (3 укосных цикла с провалами ≥0.15),
пар (NDVI у пола + BSI), мало сцен → uncertain; сверка expected_class
с записанной культурой. classify_curve — чистая функция; БД — только для
recorded_culture.
"""

from datetime import date

from app.ai_assistant import crop_classifier as cc
from tests.test_satellite import make_field, make_satellite_source

YEAR = 2026


def curve(points: list[tuple[int, int, float]]) -> list[tuple[date, float]]:
    """[(год, месяц, NDVI)] → [(дата, NDVI)] по возрастанию."""
    return sorted((date(y, m, 1), v) for y, m, v in points)


def winter_curve() -> list[tuple[date, float]]:
    # озимая: сев осень 2025, подъём окт–ноя, зима, старт апреля, пик июнь
    return curve(
        [
            (2025, 9, 0.18),
            (2025, 10, 0.38),
            (2025, 11, 0.45),
            (2026, 3, 0.30),
            (2026, 4, 0.42),
            (2026, 5, 0.62),
            (2026, 6, 0.81),
            (2026, 7, 0.60),
        ]
    )


def spring_curve() -> list[tuple[date, float]]:
    # яровая: всходы май, один пик июнь–июль, осени 2025 нет (низкий NDVI)
    return curve(
        [
            (2025, 10, 0.14),
            (2025, 11, 0.11),
            (2026, 5, 0.25),
            (2026, 5, 0.41),
            (2026, 6, 0.68),
            (2026, 7, 0.75),
            (2026, 7, 0.55),
        ]
    )


def grass_curve() -> list[tuple[date, float]]:
    # травы: 3 укоса — пик/провал/пик/провал/пик
    return curve(
        [
            (2025, 10, 0.15),
            (2026, 5, 0.55),
            (2026, 5, 0.72),  # пик 1
            (2026, 6, 0.40),  # провал −0.32 (укос)
            (2026, 7, 0.66),  # пик 2
            (2026, 8, 0.38),  # провал −0.28
            (2026, 9, 0.60),  # пик 3
            (2026, 9, 0.42),
        ]
    )


def fallow_curve() -> list[tuple[date, float]]:
    return curve(
        [
            (2025, 10, 0.12),
            (2025, 11, 0.10),
            (2026, 5, 0.18),
            (2026, 6, 0.22),
            (2026, 7, 0.25),
            (2026, 8, 0.20),
        ]
    )


class TestClassifyCurve:
    def test_winter(self) -> None:
        r = cc.classify_curve(winter_curve(), season_year=YEAR)
        assert r.crop_class == cc.WINTER_GRAIN and r.confidence >= 0.6
        assert any("осенний подъём" in e for e in r.evidences)

    def test_winter_by_april_start(self) -> None:
        """Без выраженной осени (1 сцена) — апрельский старт тоже доказывает."""
        pts = [
            (2025, 10, 0.30),
            *[
                (y, m, v)
                for y, m, v in [
                    (2026, 4, 0.38),
                    (2026, 5, 0.6),
                    (2026, 6, 0.78),
                    (2026, 7, 0.5),
                    (2026, 3, 0.2),
                    (2026, 2, 0.2),
                ]
            ],
        ]
        r = cc.classify_curve(curve(pts), season_year=YEAR)
        assert r.crop_class == cc.WINTER_GRAIN
        assert any("апреля" in e for e in r.evidences)

    def test_spring(self) -> None:
        r = cc.classify_curve(spring_curve(), season_year=YEAR)
        assert r.crop_class == cc.SPRING_GRAIN
        assert any("главный пик" in e for e in r.evidences)

    def test_grass(self) -> None:
        r = cc.classify_curve(grass_curve(), season_year=YEAR)
        assert r.crop_class == cc.PERENNIAL_GRASS
        assert any("укосн" in e for e in r.evidences)

    def test_fallow_with_bsi(self) -> None:
        bsi = [(date(2026, m, 1), 0.25) for m in range(5, 9)]
        r = cc.classify_curve(fallow_curve(), bsi=bsi, season_year=YEAR)
        assert r.crop_class == cc.FALLOW and r.confidence == 0.8
        assert any("BSI" in e for e in r.evidences)

    def test_fallow_without_bsi_weaker(self) -> None:
        r = cc.classify_curve(fallow_curve(), season_year=YEAR)
        assert r.crop_class == cc.FALLOW and r.confidence == 0.6

    def test_too_few_scenes_uncertain(self) -> None:
        pts = curve([(2026, 5, 0.5), (2026, 6, 0.7), (2026, 7, 0.6)])
        r = cc.classify_curve(pts, season_year=YEAR)
        assert r.crop_class == cc.UNCERTAIN
        assert any("мало" in e for e in r.evidences)

    def test_no_data(self) -> None:
        r = cc.classify_curve([], season_year=YEAR)
        assert r.crop_class == cc.UNCERTAIN


class TestExpectedClass:
    def test_markers(self) -> None:
        assert cc.expected_class("Озимая пшеница", "main") == cc.WINTER_GRAIN
        assert cc.expected_class("Рожь озимая", "main") == cc.WINTER_GRAIN
        assert cc.expected_class("Многолетние травы", "main") == cc.PERENNIAL_GRASS
        assert cc.expected_class("Люцерна", "main") == cc.PERENNIAL_GRASS
        assert cc.expected_class("Пар", "fallow") == cc.FALLOW
        # вид внутри яровых не сверяем
        assert cc.expected_class("Пшеница", "main") is None
        assert cc.expected_class("Гречиха", "main") is None
        assert cc.expected_class(None, None) is None


class TestRecordedAndSeason:
    def test_recorded_culture(self, db_session) -> None:
        from app.models.agronomy import CropCycle
        from app.models.references import Culture

        make_satellite_source(db_session)
        field_id = make_field(db_session)
        culture = Culture(name="Клевер", kind="main", forage_flag=False, crop_group="grass")
        db_session.add(culture)
        db_session.flush()
        db_session.add(CropCycle(field_id=field_id, year=2026, culture_id=culture.id, kind="main"))
        db_session.commit()
        assert cc.recorded_culture(db_session, field_id, 2026) == ("Клевер", "main")
        assert cc.recorded_culture(db_session, field_id, 2025) == (None, None)
        assert cc.expected_class(*cc.recorded_culture(db_session, field_id, 2026)) == (
            cc.PERENNIAL_GRASS
        )

    def test_classify_field_season_uncertain_without_scenes(self, db_session) -> None:
        make_satellite_source(db_session)
        field_id = make_field(db_session)
        r = cc.classify_field_season(db_session, field_id, 2026)
        assert r.crop_class == cc.UNCERTAIN
        assert r.confidence == 0.0

    def test_classify_field_season_multiyear(self, db_session) -> None:
        """Уверенность усиливается согласием сезонов истории (травы 2 года)."""
        from app.models.integrations import SatelliteScene

        make_satellite_source(db_session)
        field_id = make_field(db_session)

        def add(year: int) -> None:
            for m, v in [(10, 0.15), (5, 0.7), (6, 0.4), (7, 0.65), (8, 0.35), (9, 0.55)]:
                db_session.add(
                    SatelliteScene(
                        field_id=field_id,
                        index="ndvi",
                        captured_at=date(year - 1 if m == 10 else year, m, 1),
                        resolution_m=10,
                        index_min=v - 0.1,
                        index_avg=v,
                        index_max=v + 0.1,
                        cloud_pct=0.0,
                        composite_days=1,
                        source_id="satellite.sentinel2-l2a",
                        trust=1.0,
                    )
                )

        add(2025)
        add(2026)
        db_session.commit()
        r = cc.classify_field_season(db_session, field_id, 2026)
        assert r.crop_class == cc.PERENNIAL_GRASS
        assert any("история" in e for e in r.evidences)
        # 0.5·0.8 + 0.5·1.0 = 0.9
        assert r.confidence == 0.9
