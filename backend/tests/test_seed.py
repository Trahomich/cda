"""Тесты данных сидов справочников (задача MVP-2) — без БД.

Проверка самого наполнения БД (идемпотентность ON CONFLICT) выполнена
вручную на PostgreSQL 17 + PostGIS 3.5 (см. итог задачи #20).
"""

from app.seed import CULTURES, PREDECESSOR_RULES, SOURCES


def test_cultures_count_in_mvp_range() -> None:
    # 03 §5.2: MVP ограничивается 8–12 основными культурами средней полосы
    assert 8 <= len(CULTURES) <= 12


def test_cultures_names_unique() -> None:
    names = [c["name"] for c in CULTURES]
    assert len(names) == len(set(names))


def test_cultures_kinds_valid() -> None:
    for culture in CULTURES:
        # допустимые виды культур = CHECK ck_ref_culture_kind (fallow — пар,
        # полноправный элемент севооборота, RULE-AGRO-03)
        assert culture["kind"] in {"main", "forage", "catch", "fallow"}
        assert isinstance(culture["forage_flag"], bool)
        assert culture["crop_group"]


def test_mvp_cultures_present() -> None:
    names = {c["name"] for c in CULTURES}
    required = {
        "Озимая пшеница",
        "Ячмень",
        "Кукуруза",
        "Подсолнечник",
        "Соя",
        "Рапс",
        "Гречиха",
        "Горох",
        "Картофель",
        "Многолетние травы",
    }
    assert required <= names


def test_sources_mvp_set() -> None:
    ids = {s["source_id"] for s in SOURCES}
    assert ids == {"weather.open-meteo", "satellite.sentinel2-l2a", "manual"}
    for source in SOURCES:
        assert source["enabled"] is True
