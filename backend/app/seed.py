"""Сид-скрипт справочников MVP (задача MVP-2).

Наполняет ref_culture (культуры средней полосы, 03 §5.2: 8–12 основных)
и ref_source (адаптеры MVP, 05 §2). Идемпотентен: повторный запуск не
создаёт дубликатов (ON CONFLICT DO NOTHING по естественным ключам).

Запуск: python -m app.cli seed-references
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.references import Culture, PredecessorRule, Source

# Культуры средней полосы/юга (v1-08 #54, замечание №2): базовый набор
# хозяйства ЖАМ/АЗА — name, kind, forage_flag, crop_group, color_hex, repeat_years.
# kind: main — основная, forage — кормовая, catch — сидерат/промежуточная,
#  fallow — пар, полноправный элемент цепочки севооборота (RULE-AGRO-03).
# repeat_years — минимальный возврат культуры, лет (RULE-AGRO-02, 03 §3.1:
# default 3, рапс 4, картофель 5).
# color_hex — стабильный контрастный цвет на карте Leaflet (#rrggbb):
# зерновые — жёлто-янтарные, масличные — яркие, бобовые/травы — зелёные,
# пропашные — тёплые, пар чистый — серый, пар занятый — бежевый.
CULTURES: list[dict] = [
    # зерновые
    {
        "name": "Озимая пшеница",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#e8c46b",
    },
    {
        "name": "Яровая пшеница",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#f4e04d",
    },
    {
        "name": "Ячмень",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#d9a441",
    },
    {
        "name": "Ячмень озимый",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#bf8f3f",
    },
    {
        "name": "Рожь",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#a97b22",
    },
    {
        "name": "Овёс",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#efe0a8",
    },
    {
        "name": "Тритикале",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#cbb25f",
    },
    {
        "name": "Гречиха",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#e29a5e",
    },
    {
        "name": "Просо",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#f2b469",
    },
    {
        "name": "Кукуруза",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "cereals",
        "color_hex": "#f28c28",
    },
    {
        "name": "Кукуруза на силос",
        "kind": "forage",
        "forage_flag": True,
        "crop_group": "forage",
        "color_hex": "#e5761b",
    },
    # масличные
    {
        "name": "Подсолнечник",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "oilseeds",
        "color_hex": "#ffb300",
    },
    {
        "name": "Рапс",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "oilseeds",
        "color_hex": "#d4e157",
        "repeat_years": 4,
    },
    {
        "name": "Рапс озимый",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "oilseeds",
        "color_hex": "#c0ca33",
        "repeat_years": 4,
    },
    # бобовые
    {
        "name": "Соя",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "legumes",
        "color_hex": "#8bc34a",
    },
    {
        "name": "Горох",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "legumes",
        "color_hex": "#66bb6a",
    },
    {
        "name": "Чечевица",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "legumes",
        "color_hex": "#2e8b57",
    },
    # пропашные
    {
        "name": "Картофель",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "tubers",
        "color_hex": "#e57373",
        "repeat_years": 5,
    },
    {
        "name": "Свёкла сахарная",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "root_crops",
        "color_hex": "#ba68c8",
    },
    {
        "name": "Овощи открытого грунта",
        "kind": "main",
        "forage_flag": False,
        "crop_group": "vegetables",
        "color_hex": "#26a69a",
    },
    # кормовые травы
    {
        "name": "Многолетние травы",
        "kind": "forage",
        "forage_flag": True,
        "crop_group": "grasses",
        "color_hex": "#43a047",
    },
    {
        "name": "Люцерна",
        "kind": "forage",
        "forage_flag": True,
        "crop_group": "grasses",
        "color_hex": "#558b2f",
    },
    {
        "name": "Клевер с тимофеевкой",
        "kind": "forage",
        "forage_flag": True,
        "crop_group": "grasses",
        "color_hex": "#9ccc65",
    },
    {
        "name": "Однолетние травы",
        "kind": "catch",
        "forage_flag": True,
        "crop_group": "grasses",
        "color_hex": "#aed581",
    },
    {
        "name": "Горчица (сидерат)",
        "kind": "catch",
        "forage_flag": False,
        "crop_group": "other",
        "color_hex": "#c5e1a5",
        "repeat_years": 3,
    },
    # пары (RULE-AGRO-03)
    {
        "name": "Пар",
        "kind": "fallow",
        "forage_flag": False,
        "crop_group": "other",
        "color_hex": "#c8c8c8",
        "repeat_years": 3,
    },
    {
        "name": "Пар занятый",
        "kind": "fallow",
        "forage_flag": False,
        "crop_group": "other",
        "color_hex": "#d9d0b8",
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

    Идемпотентность — ON CONFLICT:
    ref_culture по name — DO UPDATE SET color_hex (v1-08 #54: актуализация
    стабильного цвета у уже развёрнутых БД; прочие поля существующих строк
    не трогаем — счётчик вставок = прирост числа строк);
    ref_source по source_id — DO NOTHING;
    ref_predecessor_matrix по паре (culture_id, predecessor_id) — DO NOTHING.
    """
    before = db.scalar(select(func.count()).select_from(Culture)) or 0
    db.execute(
        pg_insert(Culture)
        .values(CULTURES)
        .on_conflict_do_update(
            index_elements=["name"], set_={"color_hex": pg_insert(Culture).excluded.color_hex}
        )
    )
    after = db.scalar(select(func.count()).select_from(Culture)) or 0
    inserted_cultures = after - before

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
