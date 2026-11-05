"""Сервис домена «севооборот»: планы и циклы культур (задача MVP-8, API-08..11).

Бизнес-правила — docs/03 §3.1 (RULE-AGRO-01..04), чистые проверки — rules.py.
Вердикт матрицы совместимости — ref_predecessor_matrix (verdict ok/warn/ban;
отсутствующая пара — ok, матрица редактируемая, 03 §6.4). Минимальный возврат
культуры — ref_culture.repeat_years (миграция 0009, «правила как данные»).

Ошибки — RFC 7807 с доменными расширениями (docs/06 §5): 422 rule_violation
несёт extra {rule_id, severity, rationale, entity}.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.agronomy import CropCycle, Field, RotationPlan
from app.models.auth import AuditRecord, User
from app.models.references import Culture, PredecessorRule
from app.modules.rotation import rules
from app.modules.rotation.rules import Violation
from app.modules.rotation.schemas import CYCLE_KINDS, PLAN_STATUSES, PlanItemDraft

# Горизонт плана 2–10 лет включительно (docs/03 §1.2)
PLAN_MIN_YEARS = 2
PLAN_MAX_YEARS = 10


def get_plan(db: Session, plan_id: uuid.UUID) -> RotationPlan:
    plan = db.get(RotationPlan, plan_id)
    if plan is None:
        raise AppError(
            f"План севооборота {plan_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return plan


def get_field(db: Session, field_id: uuid.UUID) -> Field:
    field = db.get(Field, field_id)
    if field is None:
        raise AppError(
            f"Поле {field_id} не найдено",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return field


def get_culture(db: Session, culture_id: uuid.UUID) -> Culture:
    culture = db.get(Culture, culture_id)
    if culture is None:
        raise AppError(
            f"Культура {culture_id} не найдена в справочнике",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return culture


def _rule_violation(violation: Violation, entity: str) -> AppError:
    """422 rule_violation с extra {rule_id, severity, rationale, entity}."""
    return AppError(
        violation.rationale,
        code="rule_violation",
        title="Нарушено бизнес-правило",
        status_code=422,
        extra={
            "rule_id": violation.rule_id,
            "severity": violation.severity,
            "rationale": violation.rationale,
            "entity": entity,
        },
    )


def _audit(db: Session, *, user: User, entity: str, entity_id: uuid.UUID,
           action: str, changed: dict) -> None:
    db.add(
        AuditRecord(
            entity=entity, entity_id=entity_id, user_id=user.id, action=action, changed=changed
        )
    )


def _matrix_verdict(db: Session, culture_id: uuid.UUID,
                    predecessor_id: uuid.UUID) -> tuple[str | None, str | None]:
    """Вердикт пары (predecessor → culture) из ref_predecessor_matrix."""
    row = db.execute(
        select(PredecessorRule.verdict, PredecessorRule.comment).where(
            PredecessorRule.culture_id == culture_id,
            PredecessorRule.predecessor_id == predecessor_id,
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


def _last_cycle_before(db: Session, field_id: uuid.UUID, year: int) -> CropCycle | None:
    """Последний цикл поля с year < заданного, любого kind (RULE-AGRO-03)."""
    return db.scalars(
        select(CropCycle)
        .where(CropCycle.field_id == field_id, CropCycle.year < year)
        .order_by(CropCycle.year.desc(), CropCycle.created_at.desc(), CropCycle.id.desc())
        .limit(1)
    ).first()


def _main_cycle_year(db: Session, field_id: uuid.UUID, year: int) -> int | None:
    """Год существующего main-цикла поля (RULE-AGRO-04), если есть."""
    cycle = db.scalars(
        select(CropCycle)
        .where(CropCycle.field_id == field_id, CropCycle.year == year, CropCycle.kind == "main")
        .limit(1)
    ).first()
    return cycle.year if cycle else None


def _last_year_of_culture(db: Session, field_id: uuid.UUID, culture_id: uuid.UUID,
                          before_year: int) -> int | None:
    """Год последнего цикла с этой культурой раньше before_year (RULE-AGRO-02)."""
    return db.scalar(
        select(func.max(CropCycle.year)).where(
            CropCycle.field_id == field_id,
            CropCycle.culture_id == culture_id,
            CropCycle.year < before_year,
        )
    )


def validate_cycle(
    db: Session,
    *,
    field_id: uuid.UUID,
    year: int,
    culture_id: uuid.UUID,
    kind: str,
    out_of_rotation: bool = False,
    reason: str | None = None,
) -> tuple[Culture, CropCycle | None, list[Violation]]:
    """Проверки RULE-AGRO-01..04 для нового цикла.

    Возвращает (культура, предшественник, warn-нарушения). Ban-нарушения
    и нарушения, требующие reason, поднимаются как 422 rule_violation.
    """
    if kind not in CYCLE_KINDS:
        raise AppError(
            f"Неизвестный kind цикла «{kind}» (допустимо: {', '.join(CYCLE_KINDS)})",
            code="invalid_kind",
            title="Некорректный запрос",
            status_code=400,
        )
    culture = get_culture(db, culture_id)

    # RULE-AGRO-03: согласование kind цикла и вида культуры
    if v := rules.check_kind_culture(kind=kind, culture_kind=culture.kind,
                                     culture_name=culture.name):
        raise _rule_violation(v, entity="crop_cycle")
    # RULE-AGRO-03: цикл вне севооборота — с причиной
    if v := rules.check_out_of_rotation(out_of_rotation=out_of_rotation, reason=reason):
        raise _rule_violation(v, entity="crop_cycle")

    # RULE-AGRO-04: один main-цикл на поле+год (предпроверка; гонки ловит partial UQ)
    if v := rules.check_main_cycle_unique(
        year=year, existing_main_year=_main_cycle_year(db, field_id, year)
    ):
        raise _rule_violation(v, entity="crop_cycle")

    warnings: list[Violation] = []
    predecessor = _last_cycle_before(db, field_id, year)
    if predecessor is not None:
        pred_culture = db.get(Culture, predecessor.culture_id)
        verdict, comment = _matrix_verdict(db, culture_id, predecessor.culture_id)
        # RULE-AGRO-01: матрица совместимости
        if v := rules.check_predecessor(
            verdict,
            culture_name=culture.name,
            predecessor_name=pred_culture.name if pred_culture else None,
            year=year,
            comment=comment,
        ):
            if v.severity == rules.SEVERITY_BAN:
                raise _rule_violation(v, entity="crop_cycle")
            warnings.append(v)
    # RULE-AGRO-02: минимальный возврат культуры
    last_year = _last_year_of_culture(db, field_id, culture_id, before_year=year)
    if last_year is not None:
        if v := rules.check_repeat_years(
            year=year, last_year=last_year, repeat_years=culture.repeat_years,
            culture_name=culture.name,
        ):
            warnings.append(v)

    # warn-нарушения допустимы только с комментарием агронома (03 §3.1)
    if warnings and not (reason and reason.strip()):
        first = warnings[0]
        raise _rule_violation(first, entity="crop_cycle")
    return culture, predecessor, warnings


def create_cycle(
    db: Session,
    *,
    user: User,
    field_id: uuid.UUID,
    year: int,
    culture_id: uuid.UUID,
    kind: str,
    planned_yield_c_ha: float | None = None,
    planned_harvest_date=None,  # noqa: ANN001 — date | None
    out_of_rotation: bool = False,
    reason: str | None = None,
) -> tuple[CropCycle, list[Violation]]:
    """API-11: добавить цикл культуры (RULE-AGRO-01..04, авто-predecessor).

    predecessor_cycle_id заполняет система: последний цикл поля с year < нового
    (любого kind, 03 §1.3). Возвращает (цикл, warn-нарушения с reason).
    """
    get_field(db, field_id)
    _culture, predecessor, warnings = validate_cycle(
        db,
        field_id=field_id,
        year=year,
        culture_id=culture_id,
        kind=kind,
        out_of_rotation=out_of_rotation,
        reason=reason,
    )
    cycle = CropCycle(
        field_id=field_id,
        year=year,
        culture_id=culture_id,
        kind=kind,
        predecessor_cycle_id=predecessor.id if predecessor else None,
        planned_yield_c_ha=planned_yield_c_ha,
        planned_harvest_date=planned_harvest_date,
        out_of_rotation=out_of_rotation,
        reason=reason,
    )
    db.add(cycle)
    _audit(
        db,
        user=user,
        entity="crop_cycle",
        entity_id=cycle.id,
        action="create",
        changed={"field_id": str(field_id), "year": year, "kind": kind,
                 "culture_id": str(culture_id), "reason": reason},
    )
    try:
        db.commit()
    except IntegrityError as exc:  # гонка двух main-циклов (partial UQ, RULE-AGRO-04)
        db.rollback()
        v = rules.check_main_cycle_unique(year=year, existing_main_year=year)
        assert v is not None
        raise _rule_violation(v, entity="crop_cycle") from exc
    db.refresh(cycle)
    return cycle, warnings


def list_plans(
    db: Session,
    *,
    field_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[RotationPlan], int]:
    """API-08: список планов севооборота (фильтры field_id, status)."""
    filters = []
    if field_id is not None:
        filters.append(RotationPlan.field_id == field_id)
    if status is not None:
        if status not in PLAN_STATUSES:
            raise AppError(
                f"Неизвестный статус плана «{status}» (допустимо: "
                f"{', '.join(PLAN_STATUSES)})",
                code="invalid_status",
                title="Некорректный запрос",
                status_code=400,
            )
        filters.append(RotationPlan.status == status)
    total = db.scalar(select(func.count()).select_from(RotationPlan).where(*filters))
    plans = db.scalars(
        select(RotationPlan)
        .where(*filters)
        .order_by(RotationPlan.created_at.desc(), RotationPlan.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(plans), int(total or 0)


def plan_cycles(db: Session, plan: RotationPlan) -> list[CropCycle]:
    """items плана: циклы поля в диапазоне лет [year_from, year_to], по годам."""
    return list(
        db.scalars(
            select(CropCycle)
            .where(
                CropCycle.field_id == plan.field_id,
                CropCycle.year >= plan.year_from,
                CropCycle.year <= plan.year_to,
                CropCycle.kind == "main",  # по одному циклу на год (03 §1.2)
            )
            .order_by(CropCycle.year)
        ).all()
    )


def _validate_plan_items(
    db: Session, field_id: uuid.UUID, items: list[PlanItemDraft]
) -> list[Violation]:
    """Dry-run черновика items[]: цепочка проверяется по RULE-AGRO-01..04.

    Проверяется вся последовательность (включая уже существующие циклы поля
    как историю), записи не создаются; нарушения возвращаются списком.
    """
    violations: list[Violation] = []
    existing = {
        c.year: c
        for c in db.scalars(
            select(CropCycle)
            .where(CropCycle.field_id == field_id, CropCycle.kind == "main")
            .order_by(CropCycle.year)
        ).all()
    }
    # цепочка: существующие main-циклы, затем черновик по годам
    drafts = sorted(items, key=lambda i: i.year)
    chain: list[tuple[int, Culture, PlanItemDraft | None]] = [
        (c.year, db.get_one(Culture, c.culture_id), None) for c in existing.values()
    ]
    main_years: set[int] = set(existing)
    for draft in drafts:
        culture = db.get(Culture, draft.culture_id)
        if culture is None:
            raise AppError(
                f"Культура {draft.culture_id} не найдена в справочнике",
                code="not_found",
                title="Ресурс не найден",
                status_code=404,
            )
        chain.append((draft.year, culture, draft))
    for pos, (year, culture, draft) in enumerate(chain):
        if draft is not None and year in main_years:
            v = rules.check_main_cycle_unique(year=year, existing_main_year=year)
            if v:
                violations.append(v)
        if draft is None:
            continue
        main_years.add(year)
        if v := rules.check_kind_culture(kind=draft.kind, culture_kind=culture.kind,
                                         culture_name=culture.name):
            violations.append(v)
        if v := rules.check_out_of_rotation(
            out_of_rotation=draft.out_of_rotation, reason=draft.reason
        ):
            violations.append(v)
        # предшественник — предыдущий цикл цепочки (RULE-AGRO-01/03)
        if pos > 0:
            prev_year, prev_culture, _ = chain[pos - 1]
            verdict, comment = _matrix_verdict(db, culture.id, prev_culture.id)
            if v := rules.check_predecessor(
                verdict,
                culture_name=culture.name,
                predecessor_name=prev_culture.name,
                year=year,
                comment=comment,
            ):
                violations.append(v)
        # RULE-AGRO-02: возврат той же культуры в цепочке
        same_years = [y for y, c, _ in chain[:pos] if c.id == culture.id]
        if same_years:
            last_year = max(same_years)
            if v := rules.check_repeat_years(
                year=year,
                last_year=last_year,
                repeat_years=culture.repeat_years,
                culture_name=culture.name,
            ):
                violations.append(v)
    return violations


def create_plan(
    db: Session,
    *,
    user: User,
    field_id: uuid.UUID,
    year_from: int,
    year_to: int,
    items: list[PlanItemDraft],
) -> tuple[RotationPlan, list[Violation]]:
    """API-09: создать план севооборота; ответ содержит список нарушений.

    Активный (не archived) план на пересекающийся диапазон лет на поле один —
    409 overlapping_plan. items[] — черновик: dry-run без записи циклов.
    """
    get_field(db, field_id)
    if year_to < year_from:
        raise AppError(
            "year_to должен быть не меньше year_from (docs/03 §1.2)",
            code="invalid_plan_range",
            title="Некорректный запрос",
            status_code=400,
        )
    length = year_to - year_from + 1
    if not PLAN_MIN_YEARS <= length <= PLAN_MAX_YEARS:
        raise AppError(
            f"Горизонт плана должен быть {PLAN_MIN_YEARS}–{PLAN_MAX_YEARS} лет "
            f"(получено {length}: {year_from}–{year_to})",
            code="invalid_plan_range",
            title="Нарушено бизнес-правило",
            status_code=422,
        )
    overlapping = db.scalars(
        select(RotationPlan).where(
            RotationPlan.field_id == field_id,
            RotationPlan.status != "archived",
            RotationPlan.year_from <= year_to,
            RotationPlan.year_to >= year_from,
        )
    ).first()
    if overlapping is not None:
        raise AppError(
            f"На поле уже есть активный план {overlapping.year_from}–{overlapping.year_to} гг. "
            f"(статус {overlapping.status}); сначала переведите его в archived",
            code="overlapping_plan",
            title="Конфликт состояния",
            status_code=409,
        )
    violations = _validate_plan_items(db, field_id, items) if items else []
    plan = RotationPlan(field_id=field_id, year_from=year_from, year_to=year_to)
    db.add(plan)
    _audit(
        db,
        user=user,
        entity="rotation_plan",
        entity_id=plan.id,
        action="create",
        changed={"field_id": str(field_id), "year_from": year_from,
                 "year_to": year_to, "dry_run_items": len(items)},
    )
    db.commit()
    db.refresh(plan)
    return plan, violations


def approve_plan(db: Session, *, user: User, plan: RotationPlan) -> RotationPlan:
    """API-10: согласование руководителем (draft → approved).

    Повторное согласование или архив — 409 invalid_state (аудит status_change).
    """
    if plan.status != "draft":
        raise AppError(
            f"Согласовать можно только план в статусе draft (текущий: {plan.status})",
            code="invalid_state",
            title="Конфликт состояния",
            status_code=409,
        )
    previous = plan.status
    from datetime import datetime, timezone

    plan.status = "approved"
    plan.approved_by = user.fio
    plan.approved_at = datetime.now(timezone.utc)
    _audit(
        db,
        user=user,
        entity="rotation_plan",
        entity_id=plan.id,
        action="status_change",
        changed={"status_from": previous, "status_to": "approved"},
    )
    db.commit()
    db.refresh(plan)
    return plan

