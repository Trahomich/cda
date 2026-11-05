"""Сид-скрипт справочников MVP (задача MVP-2).

Наполняет ref_culture (культуры средней полосы, 03 §5.2: 8–12 основных)
и ref_source (адаптеры MVP, 05 §2). Идемпотентен: повторный запуск не
создаёт дубликатов (ON CONFLICT DO NOTHING по естественным ключам).

Запуск: python -m app.cli seed-references
"""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.references import Culture, Source

# Культуры средней полосы: name, kind, forage_flag, crop_group
# (kind: main — основная, forage — кормовая, catch — сидерат/промежуточная,
#  fallow — пар, полноправный элемент цепочки севооборота, RULE-AGRO-03)
# repeat_years — минимальный возврат культуры, лет (RULE-AGRO-02, 03 §3.1:
# default 3, рапс 4, картофель 5)
CULTURES: list[dict] = [
    {"name": "Озимая пшеница", "kind": "main", "forage_flag": False, "crop_group": "cereals"},
    {"name": "Ячмень", "kind": "main", "forage_flag": False, "crop_group": "cereals"},
    {"name": "Кукуруза", "kind": "main", "forage_flag": False, "crop_group": "cereals"},
    {"name": "Подсолнечник", "kind": "main", "forage_flag": False, "crop_group": "oilseeds"},
    {"name": "Соя", "kind": "main", "forage_flag": False, "crop_group": "legumes"},
        {"name": "Рапс", "kind": "main", "forage_flag": False, "crop_group": "oilseeds",
     "repeat_years": 4},
    {"name": "Гречиха", "kind": "main", "forage_flag": False, "crop_group": "cereals"},
    {"name": "Горох", "kind": "main", "forage_flag": False, "crop_group": "legumes"},
        {"name": "Картофель", "kind": "main", "forage_flag": False, "crop_group": "tubers",
     "repeat_years": 5},
    {"name": "Многолетние травы", "kind": "forage", "forage_flag": True, "crop_group": "grasses"},
        {"name": "Горчица (сидерат)", "kind": "catch", "forage_flag": False, "crop_group": "other",
     "repeat_years": 3},
        {"name": "Пар", "kind": "fallow", "forage_flag": False, "crop_group": "other",
     "repeat_years": 3},
]

# Адаптеры MVP: source_id (05 §2), kind, enabled
SOURCES: list[dict] = [
    {"source_id": "weather.open-meteo", "kind": "weather", "enabled": True},
    {"source_id": "satellite.sentinel2-l2a", "kind": "satellite", "enabled": True},
    {"source_id": "manual", "kind": "manual", "enabled": True},
]


def seed_references(db: Session) -> dict[str, int]:
    """Вставить справочные записи; вернуть счётчики добавленного.

    Идемпотентность — ON CONFLICT DO NOTHING:
    ref_culture по name, ref_source по source_id.
    """
    cultures = (
        pg_insert(Culture)
        .values(CULTURES)
        .on_conflict_do_nothing(index_elements=["name"])
        .returning(Culture.id)
    )
    inserted_cultures = len(db.execute(cultures).fetchall())

    sources = (
        pg_insert(Source)
        .values(SOURCES)
        .on_conflict_do_nothing(index_elements=["source_id"])
        .returning(Source.id)
    )
    inserted_sources = len(db.execute(sources).fetchall())

    db.commit()
    return {"ref_culture": inserted_cultures, "ref_source": inserted_sources}
