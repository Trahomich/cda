"""Справочник препаратов и удобрений реального хозяйства (v2-01, #71).

CLI ``python -m app.cli seed-inputs``: наполняет ``dat_agro_input``
(ENT-AGRO-INPUT, docs/03 §1.4) реальными препаратами — удобрения с
NPK-составом и средства защиты растений (СЗР) с действующим веществом,
концентрацией и сроком ожидания (RULE-AGRO-08), ограничениями по культурам
и числу обработок за сезон (RULE-AGRO-10).

Идемпотентность — ON CONFLICT (kind, name) DO NOTHING (docs/06 §7.2):
повторный запуск не создаёт дублей, счётчик вставок — прирост числа строк.
Составы/д.в./сроки — по типовым этикеткам препаратов; реальных закупочных
данных пользователь не передавал (допущение как в seed-rotation #66).

``approved_crops`` для позиций с ограничением по культурам заполняется
id культур из ref_culture по имени (отсутствующие в справочнике культуры
просто не попадают в список; пусто = без ограничений, docs/03 §1.4).
"""

import logging
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.agronomy import AgroInput
from app.models.references import Culture

logger = logging.getLogger(__name__)

# ---------- справочник: удобрения (kind=fertilizer, unit=kg_ha/l_ha) ----------
# n_pct / p2o5_pct / k2o_pct — по фактическому содержанию действующего
# вещества, % (массовая доля); waiting_days у удобрений NULL (RULE-AGRO-08).

FERTILIZERS: list[dict] = [
    {
        "name": "Мочевина (карбамид)",
        "n_pct": 46.2,
        "p2o5_pct": 0.0,
        "k2o_pct": 0.0,
        "restrictions": "Вносить с заделкой; не смешивать с аммиачной селитрой",
    },
    {
        "name": "Аммиачная селитра",
        "n_pct": 34.5,
        "p2o5_pct": 0.0,
        "k2o_pct": 0.0,
        "restrictions": "Ограничения по хранению и применению (нормативные)",
    },
    {
        "name": "КАС-32 (карбамидо-аммиачная смесь)",
        "n_pct": 32.0,
        "p2o5_pct": 0.0,
        "k2o_pct": 0.0,
        "unit": "l_ha",
        "restrictions": "Жидкое; не применять при T < 0 °C",
    },
    {
        "name": "Сульфат аммония",
        "n_pct": 21.0,
        "p2o5_pct": 0.0,
        "k2o_pct": 0.0,
        "restrictions": "Подкисляет почву; на кислых почвах с известкованием",
    },
    {"name": "Аммофос", "n_pct": 12.0, "p2o5_pct": 52.0, "k2o_pct": 0.0, "restrictions": None},
    {
        "name": "Диаммофоска 10:26:26",
        "n_pct": 10.0,
        "p2o5_pct": 26.0,
        "k2o_pct": 26.0,
        "restrictions": None,
    },
    {
        "name": "Аммофоска 12:15:15",
        "n_pct": 12.0,
        "p2o5_pct": 15.0,
        "k2o_pct": 15.0,
        "restrictions": "Содержит серу и магний",
    },
    {
        "name": "NPK 16:16:16 (нитроаммофоска)",
        "n_pct": 16.0,
        "p2o5_pct": 16.0,
        "k2o_pct": 16.0,
        "restrictions": None,
    },
    {
        "name": "Нитроаммофоска 17:17:17",
        "n_pct": 17.0,
        "p2o5_pct": 17.0,
        "k2o_pct": 17.0,
        "restrictions": None,
    },
    {
        "name": "Хлористый калий",
        "n_pct": 0.0,
        "p2o5_pct": 0.0,
        "k2o_pct": 60.0,
        "restrictions": "Хлорсодержащее; под хлорофобные культуры — осенью",
    },
    {
        "name": "Сульфат калия",
        "n_pct": 0.0,
        "p2o5_pct": 0.0,
        "k2o_pct": 50.0,
        "restrictions": "Предпочтителен на хлорофобных культурах",
    },
    {
        "name": "Двойной суперфосфат",
        "n_pct": 0.0,
        "p2o5_pct": 45.0,
        "k2o_pct": 0.0,
        "restrictions": None,
    },
]

# ---------- справочник: СЗР (kind=ppp, unit=l_ha) ----------
# concentration — содержание действующего вещества, г/л (жидкие формы КЭ/КС/ВСК);
# waiting_days — срок ожидания до уборки, дней (RULE-AGRO-08); crop_names —
# ограничение допустимых культур (RULE-AGRO-10), пусто = без ограничений.

PPPS: list[dict] = [
    # фунгициды
    {
        "name": "Абига-Пик, ВС",
        "active_substance": "Хлорокись меди",
        "concentration": 400,
        "waiting_days": 20,
        "max_treatments_per_season": 3,
        "crop_names": [],
        "restrictions": "Не более 3 обработок за сезон",
    },
    {
        "name": "Скор, КЭ",
        "active_substance": "Дифеноконазол",
        "concentration": 250,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": "Не применять при T > 25 °C",
    },
    {
        "name": "Тилт, КЭ",
        "active_substance": "Пропиконазол",
        "concentration": 250,
        "waiting_days": 30,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": None,
    },
    {
        "name": "Топаз, КЭ",
        "active_substance": "Пенконазол",
        "concentration": 100,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": None,
    },
    {
        "name": "Фундазол, СП",
        "active_substance": "Беномил",
        "concentration": 500,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": "Риск резистентности: чередовать с другими классами",
    },
    {
        "name": "Байлетон, СП",
        "active_substance": "Триадимефон",
        "concentration": 250,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": None,
    },
    # инсектициды
    {
        "name": "Актара, ВДГ",
        "active_substance": "Тиаметоксам",
        "concentration": 250,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": "Токсичен для пчёл: обрабатывать вне лёта",
    },
    {
        "name": "Децис Профи, ВДГ",
        "active_substance": "Дельтаметрин",
        "concentration": 250,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": "Токсичен для пчёл и рыб",
    },
    {
        "name": "Каратэ Зеон, МКС",
        "active_substance": "Лямбда-цигалотрин",
        "concentration": 100,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": "Токсичен для пчёл",
    },
    {
        "name": "БИ-58 Новый, КЭ",
        "active_substance": "Диметоат",
        "concentration": 400,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": "Не применять при T > 30 °C",
    },
    {
        "name": "Конфидор Экстра, ВДГ",
        "active_substance": "Имидаклоприд",
        "concentration": 70,
        "waiting_days": 20,
        "max_treatments_per_season": 1,
        "crop_names": [],
        "restrictions": None,
    },
    {
        "name": "Фуфанон Нова, КЭ",
        "active_substance": "Малатион (карбофос)",
        "concentration": 570,
        "waiting_days": 20,
        "max_treatments_per_season": 2,
        "crop_names": [],
        "restrictions": None,
    },
    # гербициды
    {
        "name": "Раундап, ВР",
        "active_substance": "Глифосат (изопропиламинная соль)",
        "concentration": 360,
        "waiting_days": 7,
        "max_treatments_per_season": 1,
        "crop_names": [],
        "restrictions": "Только по сорнякам до посева/всходов культуры",
    },
    {
        "name": "Лонтрел 300, ВР",
        "active_substance": "Клопиралид",
        "concentration": 300,
        "waiting_days": 30,
        "max_treatments_per_season": 1,
        "crop_names": ["Озимая пшеница", "Яровая пшеница", "Ячмень", "Овёс"],
        "restrictions": "Обладает последействием на бобовые",
    },
    {
        "name": "Зеллек Супер, КЭ",
        "active_substance": "Флуазифоп-П-бутил",
        "concentration": 125,
        "waiting_days": 30,
        "max_treatments_per_season": 1,
        "crop_names": ["Соя", "Подсолнечник"],
        "restrictions": "Против злаковых сорняков на двудольных культурах",
    },
    {
        "name": "Пивот, ВК",
        "active_substance": "Имазетапир",
        "concentration": 100,
        "waiting_days": 60,
        "max_treatments_per_season": 1,
        "crop_names": ["Соя", "Горох"],
        "restrictions": "Длительное последействие: ротационные ограничения",
    },
    {
        "name": "Гранстар Про, ВДГ",
        "active_substance": "Тифенсульфурон-метил",
        "concentration": 750,
        "waiting_days": 14,
        "max_treatments_per_season": 1,
        "crop_names": [],
        "restrictions": "Только в фазе кущения зерновых",
    },
    {
        "name": "Базагран, ВР",
        "active_substance": "Бентазон",
        "concentration": 480,
        "waiting_days": 30,
        "max_treatments_per_season": 1,
        "crop_names": [],
        "restrictions": "Не применять при T < 10 °C",
    },
    # протравители семян
    {
        "name": "Максим, КС",
        "active_substance": "Флудиоксонил",
        "concentration": 25,
        "waiting_days": 0,
        "max_treatments_per_season": 1,
        "crop_names": [],
        "restrictions": "Протравитель семян; по вегетации не применять",
    },
    {
        "name": "ТМТД, ВСК",
        "active_substance": "Тирам",
        "concentration": 400,
        "waiting_days": 0,
        "max_treatments_per_season": 1,
        "crop_names": [],
        "restrictions": "Протравитель семян",
    },
]


@dataclass
class SeedInputsReport:
    """Итог сида: вставлено / уже было, по видам и всего."""

    fertilizers: int = 0
    ppps: int = 0
    inserted: int = 0
    existing: int = 0
    missing_crops: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.missing_crops is None:
            self.missing_crops = []

    def as_dict(self) -> dict:
        return asdict(self)


def seed_inputs(db: Session) -> SeedInputsReport:
    """Наполнить dat_agro_input справочником препаратов (идемпотентно)."""
    report = SeedInputsReport(
        fertilizers=len(FERTILIZERS),
        ppps=len(PPPS),
    )
    wanted = {name for item in PPPS for name in item.get("crop_names", [])}
    culture_ids = dict(db.execute(select(Culture.name, Culture.id)).fetchall())
    report.missing_crops = sorted(wanted - culture_ids.keys())

    rows: list[dict] = []
    for item in FERTILIZERS:
        rows.append(
            {
                "kind": "fertilizer",
                "name": item["name"],
                "n_pct": item["n_pct"],
                "p2o5_pct": item["p2o5_pct"],
                "k2o_pct": item["k2o_pct"],
                "active_substance": None,
                "concentration": None,
                "unit": item.get("unit", "kg_ha"),
                "waiting_days": None,  # RULE-AGRO-08: у удобрений срока ожидания нет
                "approved_crops": [],
                "max_treatments_per_season": 2,
                "restrictions": item.get("restrictions"),
            }
        )
    for item in PPPS:
        rows.append(
            {
                "kind": "ppp",
                "name": item["name"],
                "n_pct": None,
                "p2o5_pct": None,
                "k2o_pct": None,
                "active_substance": item["active_substance"],
                "concentration": item["concentration"],
                "unit": item.get("unit", "l_ha"),
                "waiting_days": item["waiting_days"],
                "approved_crops": [
                    culture_ids[name] for name in item.get("crop_names", []) if name in culture_ids
                ],
                "max_treatments_per_season": item["max_treatments_per_season"],
                "restrictions": item.get("restrictions"),
            }
        )

    stmt = (
        pg_insert(AgroInput)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["kind", "name"])
        .returning(AgroInput.id)
    )
    inserted = len(db.execute(stmt).fetchall())
    report.inserted = inserted
    report.existing = len(rows) - inserted
    db.commit()

    logger.info(
        "seed-inputs: справочник препаратов: вставлено=%d (удобрения=%d, СЗР=%d), "
        "уже было=%d; культур без соответствия: %s",
        report.inserted,
        report.fertilizers,
        report.ppps,
        report.existing,
        ", ".join(report.missing_crops) or "нет",
    )
    return report


__all__ = ["FERTILIZERS", "PPPS", "SeedInputsReport", "seed_inputs"]
