"""Чистые проверки бизнес-правил препаратов — RULE-AGRO-07/08/10 (docs/03 §3.2–3.3).

Функции не обращаются к БД: на входе — уже собранные данные, на выходе —
Violation | None (паттерн модуля rotation). rationale — «правило + входные
данные + вердикт», человекочитаемо (ADR-04).

Реализуемая часть v1 (задача MVP-11):
- RULE-AGRO-07 — доза ≥ 0 и суммарный N за сезон ≤ 180 кг д.в./га (default).
  Доля «≤ 1,3 × выноса» требует RULE-AGRO-05/06 (post-MVP) — не проверяется.
- RULE-AGRO-08 — срок ожидания СЗР до плановой уборки цикла (только spraying).
- RULE-AGRO-10 — допустимые культуры и лимит обработок действующим веществом.
"""

from dataclasses import dataclass
from datetime import date

RULE_07 = "RULE-AGRO-07"  # лимиты доз
RULE_08 = "RULE-AGRO-08"  # срок ожидания СЗР
RULE_10 = "RULE-AGRO-10"  # ограничения препарата

SEVERITY_BAN = "ban"

# Суммарная доза азота за сезон на поле, кг д.в./га (docs/03 §3.2, default
# 180 для зерновых). Норматив max_n_season[C] по культуре — post-MVP:
# в v1 лимит один для всех культур (допущение зафиксировано в задаче MVP-11).
MAX_N_SEASON_DEFAULT = 180.0


@dataclass(frozen=True)
class Violation:
    """Нарушение бизнес-правила: правило, серьёзность, человекочитаемое пояснение."""

    rule_id: str
    severity: str  # здесь всегда ban — создания/согласования не будет
    rationale: str


def nitrogen_kg_dv_ha(dose: float, unit: str, n_pct: float | None) -> float:
    """Вклад одной дозы в азотный баланс сезона, кг д.в./га.

    unit=kg_dv_ha — доза уже выражена в д.в. (азотное удобрение), n_pct не
    применяется; kg_ha / l_ha — доля N из состава (допущение: плотность 1).
    Препараты без азота (n_pct=None) вклада не дают.
    """
    if dose <= 0:
        return 0.0
    if unit == "kg_dv_ha":
        return float(dose)
    if n_pct is None:
        return 0.0
    return float(dose) * float(n_pct) / 100.0


def check_dose_non_negative(*, dose: float, input_name: str) -> Violation | None:
    """RULE-AGRO-07: отрицательные дозы запрещены валидацией (docs/03 §3.2)."""
    if dose < 0:
        return Violation(
            RULE_07,
            SEVERITY_BAN,
            f"Доза препарата «{input_name}» не может быть отрицательной "
            f"({dose}); отрицательные дозы запрещены (RULE-AGRO-07)",
        )
    return None


def check_n_season(
    *,
    current_n: float,
    new_n: float,
    limit: float = MAX_N_SEASON_DEFAULT,
    input_name: str,
) -> Violation | None:
    """RULE-AGRO-07: суммарная доза N за сезон на поле ≤ limit кг д.в./га."""
    total = current_n + new_n
    if total > limit + 1e-9:
        return Violation(
            RULE_07,
            SEVERITY_BAN,
            f"Суммарная доза азота за сезон превысит лимит: уже внесено в планах "
            f"{current_n:.1f} + «{input_name}» {new_n:.1f} = {total:.1f} кг д.в./га "
            f"> {limit:.0f} кг д.в./га (RULE-AGRO-07)",
        )
    return None


def check_waiting_days(
    *,
    planned_date: date | None,
    harvest_date: date | None,
    waiting_days: int | None,
    input_name: str,
    culture_name: str,
) -> Violation | None:
    """RULE-AGRO-08: spraying запрещён, если до плановой уборки меньше waiting_days.

    Не применяется, если у препарата нет срока ожидания или у цикла не задана
    planned_harvest_date (сравнивать не с чем — допущение v1).
    """
    if waiting_days is None or planned_date is None or harvest_date is None:
        return None
    days_left = (harvest_date - planned_date).days
    if days_left < waiting_days:
        return Violation(
            RULE_08,
            SEVERITY_BAN,
            f"Срок ожидания препарата «{input_name}» {waiting_days} дн., "
            f"до плановой уборки ({culture_name}, {harvest_date.isoformat()}) осталось "
            f"{max(days_left, 0)} дн. — обработка {planned_date.isoformat()} "
            f"слишком поздняя (RULE-AGRO-08)",
        )
    return None


def check_approved_crops(
    *,
    approved_crops: list,
    culture_id,
    input_name: str,
    culture_name: str,
) -> Violation | None:
    """RULE-AGRO-10: пусто = без ограничений, иначе культура цикла должна входить."""
    if approved_crops and culture_id not in approved_crops:
        return Violation(
            RULE_10,
            SEVERITY_BAN,
            f"Препарат «{input_name}» не разрешён для культуры «{culture_name}» "
            f"(список допустимых культур не включает её; RULE-AGRO-10)",
        )
    return None


def check_max_treatments(
    *,
    count: int,
    max_treatments: int,
    substance: str,
    input_name: str,
) -> Violation | None:
    """RULE-AGRO-10: обработок одним действующим веществом за сезон ≤ лимита.

    count — уже запланированные (не отменённые) обработки этим веществом;
    создаваемая/согласуемая — следующая по счёту.
    """
    if count + 1 > max_treatments:
        return Violation(
            RULE_10,
            SEVERITY_BAN,
            f"Превышен лимит обработок действующим веществом «{substance}» "
            f"(препарат «{input_name}»): за сезон уже запланировано {count} "
            f"из максимум {max_treatments} (RULE-AGRO-10)",
        )
    return None
