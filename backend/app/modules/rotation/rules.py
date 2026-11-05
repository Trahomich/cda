"""Чистые проверки бизнес-правил севооборота — RULE-AGRO-01..04 (docs/03 §3.1).

Функции не обращаются к БД: на входе — уже собранные данные, на выходе —
Violation | None. rationale строится по схеме «правило + входные данные +
вердикт» — сырьё для человекочитаемых объяснений (ADR-04, 03 §6.4).

Сопоставление verdict матрицы ref_predecessor_matrix: ok — допустимо,
warn — допустимо с обязательным комментарием агронома (reason),
ban — ввод блокируется (422 rule_violation).
"""

from dataclasses import dataclass

RULE_01 = "RULE-AGRO-01"  # совместимость культуры и предшественника
RULE_02 = "RULE-AGRO-02"  # минимальный возврат культуры
RULE_03 = "RULE-AGRO-03"  # пар и промежуточные культуры
RULE_04 = "RULE-AGRO-04"  # целостность цепочки

SEVERITY_BAN = "ban"
SEVERITY_WARN = "warn"

# Согласование kind цикла и kind культуры (допущение MVP-8, docs/03 §1.3:
# «культура, „пар“ или „сидерат“ (справочник культур)»; матрица в обсуждении #26)
CYCLE_KIND_CULTURE_KINDS: dict[str, frozenset[str]] = {
    "main": frozenset({"main", "forage"}),
    "catch": frozenset({"catch"}),
    "fallow": frozenset({"fallow"}),
}

VERDICTS = ("ok", "warn", "ban")


@dataclass(frozen=True)
class Violation:
    """Нарушение бизнес-правила: правило, серьёзность, человекочитаемое пояснение."""

    rule_id: str
    severity: str  # SEVERITY_BAN (блок) | SEVERITY_WARN (требует reason)
    rationale: str


def check_predecessor(
    verdict: str | None,
    *,
    culture_name: str,
    predecessor_name: str | None,
    year: int,
    comment: str | None = None,
) -> Violation | None:
    """RULE-AGRO-01: матрица совместимости «предшественник → культура».

    verdict — значение ref_predecessor_matrix для пары (predecessor, culture);
    отсутствующая запись пары считается ok (матрица редактируемая, 03 §6.4).
    """
    if verdict not in VERDICTS or predecessor_name is None:
        return None
    if verdict == "ban":
        detail = comment or "см. комментарий в справочнике совместимости"
        return Violation(
            RULE_01,
            SEVERITY_BAN,
            f"{RULE_01}: посев «{culture_name}» в {year} г. после предшественника "
            f"«{predecessor_name}» запрещён матрицей совместимости ({detail})",
        )
    if verdict == "warn":
        detail = comment or "допустимо при обосновании агронома"
        return Violation(
            RULE_01,
            SEVERITY_WARN,
            f"{RULE_01}: пара «{predecessor_name}» → «{culture_name}» ({year} г.) "
            f"допустима только с комментарием агронома: {detail}",
        )
    return None


def check_repeat_years(
    *,
    year: int,
    last_year: int,
    repeat_years: int,
    culture_name: str,
) -> Violation | None:
    """RULE-AGRO-02: повторный посев допустим при возврате ≥ repeat_years лет.

    Нарушение — уровень warn (как в RULE-AGRO-01): допустимо с reason.
    """
    gap = year - last_year
    if gap < repeat_years:
        return Violation(
            RULE_02,
            SEVERITY_WARN,
            f"{RULE_02}: возврат «{culture_name}» на поле через {gap} г. меньше "
            f"минимальных {repeat_years} г. (последний посев {last_year} г., "
            f"планируемый {year} г.) — требуется комментарий агронома",
        )
    return None


def check_kind_culture(*, kind: str, culture_kind: str, culture_name: str) -> Violation | None:
    """RULE-AGRO-03: согласование kind цикла и вида культуры в справочнике.

    main ← культура main/forage, catch ← сидерат (catch), fallow ← пар.
    """
    if culture_kind not in CYCLE_KIND_CULTURE_KINDS.get(kind, frozenset()):
        return Violation(
            RULE_03,
            SEVERITY_BAN,
            f"{RULE_03}: цикл kind={kind} несовместим с культурой «{culture_name}» "
            f"(kind={culture_kind} в справочнике)",
        )
    return None


def check_out_of_rotation(*, out_of_rotation: bool, reason: str | None) -> Violation | None:
    """RULE-AGRO-03: цикл вне севооборота требует причину."""
    if out_of_rotation and not (reason and reason.strip()):
        return Violation(
            RULE_03,
            SEVERITY_BAN,
            f"{RULE_03}: для цикла с пометкой out_of_rotation обязательна причина (поле reason)",
        )
    return None


def check_main_cycle_unique(*, year: int, existing_main_year: int | None) -> Violation | None:
    """RULE-AGRO-04: на поле и год — ровно один цикл kind=main."""
    if existing_main_year is not None:
        return Violation(
            RULE_04,
            SEVERITY_BAN,
            f"{RULE_04}: на поле в {year} г. уже есть основной цикл (kind=main); "
            "допускается ровно один main-цикл на год плюс произвольное число catch",
        )
    return None
