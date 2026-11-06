"""Сид-скрипт справочников MVP (задача MVP-2).

Наполняет ref_culture (культуры средней полосы, 03 §5.2: 8–12 основных)
и ref_source (адаптеры MVP, 05 §2). Идемпотентен: повторный запуск не
создаёт дубликатов (ON CONFLICT DO NOTHING по естественным ключам).

Запуск: python -m app.cli seed-references
"""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.references import Culture, PredecessorRule, Source

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
    {
        "name": "Рапс",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "oilseeds",
        "repeat_years": 4,
    },
    {"name": "Гречиха", "kind": "main", "forage_flag": False, "crop_group": "cereals"},
    {"name": "Горох", "kind": "main", "forage_flag": False, "crop_group": "legumes"},
    {
        "name": "Картофель",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "tubers",
        "repeat_years": 5,
    },
    {"name": "Многолетние травы", "kind": "forage", "forage_flag": True, "crop_group": "grasses"},
    {
        "name": "Горчица (сидерат)",
        "kind": "catch",
        "forage_flag": False,
        "crop_group": "other",
        "repeat_years": 3,
    },
    {
        "name": "Пар",
        "kind": "fallow",
        "forage_flag": False,
        "crop_group": "other",
        "repeat_years": 3,
    },
]

# Адаптеры MVP: source_id (05 §2), kind, enabled
SOURCES: list[dict] = [
    {"source_id": "weather.open-meteo", "kind": "weather", "enabled": True},
    {"source_id": "satellite.sentinel2-l2a", "kind": "satellite", "enabled": True},
    {"source_id": "manual", "kind": "manual", "enabled": True},
]


# Матрица совместимости «предшественник → культура» (RULE-AGRO-01, 03 §3.2):
# culture — что сеем, predecessor — что было на поле до этого.
# Демо-минимум для демонстрации нарушений в UI и dry-run API-11/12:
# минимум одна пара warn и одна ban (наблюдения #132/#134, задачи #40/#41).
PREDECESSOR_RULES: list[dict] = [
    # ban — монокультура и критически несовместимые пары
    {
        "culture": "Озимая пшеница",
        "predecessor": "Озимая пшеница",
        "verdict": "ban",
        "comment": "монокультура: накопление корневых гнилей и сорняков (демо, RULE-AGRO-01)",
    },
    {
        "culture": "Подсолнечник",
        "predecessor": "Подсолнечник",
        "verdict": "ban",
        "comment": "истощение почвы и болезни подсолнечника при повторе (демо)",
    },
    # warn — допустимо с оговоркой
    {
        "culture": "Озимая пшеница",
        "predecessor": "Ячмень",
        "verdict": "warn",
        "comment": "общие болезни злаков после ячменя — нужен протравитель (демо)",
    },
    {
        "culture": "Озимая пшеница",
        "predecessor": "Кукуруза",
        "verdict": "warn",
        "comment": "кукуруза иссушает почву — риск зимостойкости озимых (демо)",
    },
    # ok — лучшие предшественники
    {
        "culture": "Озимая пшеница",
        "predecessor": "Горох",
        "verdict": "ok",
        "comment": "парозанимающий горох — лучший предшественник озимых (демо)",
    },
    {
        "culture": "Озимая пшеница",
        "predecessor": "Пар",
        "verdict": "ok",
        "comment": "чистый пар — идеальный предшественник озимых (демо)",
    },
]


def seed_references(db: Session) -> dict[str, int]:
    """Вставить справочные записи; вернуть счётчики добавленного.

    Идемпотентность — ON CONFLICT DO NOTHING:
    ref_culture по name, ref_source по source_id,
    ref_predecessor_matrix по паре (culture_id, predecessor_id).
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

    # матрица предшественников — по id культур (справочники уже актуальны)
    culture_ids = dict(db.execute(select(Culture.name, Culture.id)).fetchall())
    rules = (
        pg_insert(PredecessorRule)
        .values(
            [
                {
                    "culture_id": culture_ids[r["culture"]],
                    "predecessor_id": culture_ids[r["predecessor"]],
                    "verdict": r["verdict"],
                    "comment": r["comment"],
                }
                for r in PREDECESSOR_RULES
            ]
        )
        .on_conflict_do_nothing(index_elements=["culture_id", "predecessor_id"])
        .returning(PredecessorRule.id)
    )
    inserted_rules = len(db.execute(rules).fetchall())

    db.commit()
    return {
        "ref_culture": inserted_cultures,
        "ref_source": inserted_sources,
        "ref_predecessor_matrix": inserted_rules,
    }
