"""Сервис домена «удобрения и СЗР» (задача MVP-11, API-14..16, docs/03 §1.4–1.5).

Бизнес-правила — docs/03 §3.2–3.3 (RULE-AGRO-07/08/10), чистые проверки —
rules.py. Планы внесения проверяются при создании и повторно при согласовании
(API-16): за время жизни черновика другие планы могли занять лимит.

Ошибки — RFC 7807 с доменными расширениями (docs/06 §5): 422 rule_violation
несёт extra {rule_id, severity, rationale, entity}.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.agronomy import AgroInput, CropCycle, Field, FieldOperation
from app.models.auth import AuditRecord, User
from app.models.references import Culture
from app.modules.inputs import rules
from app.modules.inputs.rules import Violation
from app.modules.inputs.schemas import (
    INPUT_KINDS,
    INPUT_UNITS,
    OPERATION_TYPES,
    TREATMENT_STATUSES,
)


def get_input(db: Session, input_id: uuid.UUID) -> AgroInput:
    item = db.get(AgroInput, input_id)
    if item is None:
        raise AppError(
            f"Препарат {input_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return item


def get_cycle(db: Session, cycle_id: uuid.UUID) -> CropCycle:
    cycle = db.get(CropCycle, cycle_id)
    if cycle is None:
        raise AppError(
            f"Цикл {cycle_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return cycle


def get_treatment_plan(db: Session, plan_id: uuid.UUID) -> FieldOperation:
    plan = db.get(TreatmentPlan, plan_id)
    if plan is None:
        raise AppError(
            f"План внесения {plan_id} не найден",
            code="not_found",
            title="Ресурс не найден",
            status_code=404,
        )
    return plan


def _rule_violation(violation: Violation, entity: str = "treatment_plan") -> AppError:
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


def _audit(
    db: Session, *, user: User, entity: str, entity_id: uuid.UUID, action: str, changed: dict
) -> None:
    db.add(
        AuditRecord(
            entity=entity, entity_id=entity_id, user_id=user.id, action=action, changed=changed
        )
    )


def input_npk(item: AgroInput) -> list[float] | None:
    """[% N, P2O5, K2O] из колонок БД; None, если состав не задан."""
    if item.n_pct is None and item.p2o5_pct is None and item.k2o_pct is None:
        return None
    return [float(item.n_pct or 0), float(item.p2o5_pct or 0), float(item.k2o_pct or 0)]


def create_input(
    db: Session,
    *,
    user: User,
    kind: str,
    name: str,
    npk_content: list[float | None] | None,
    active_substance: str | None,
    concentration: float | None,
    unit: str,
    waiting_days: int | None,
    approved_crops: list[uuid.UUID],
    max_treatments_per_season: int,
    restrictions: str | None,
) -> AgroInput:
    """API-14: POST /agro-inputs — запись справочника препаратов.

    Ограничения ENT-AGRO-INPUT (docs/03 §1.4): npk — только для удобрений,
    active_substance/waiting_days — только для СЗР; name уникален в рамках kind.
    """
    if kind not in INPUT_KINDS:
        raise AppError(
            f"Неизвестный kind препарата «{kind}» (допустимо: {', '.join(INPUT_KINDS)})",
            code="invalid_kind",
            title="Некорректный запрос",
            status_code=400,
        )
    if unit not in INPUT_UNITS:
        raise AppError(
            f"Неизвестная единица «{unit}» (допустимо: {', '.join(INPUT_UNITS)})",
            code="invalid_unit",
            title="Некорректный запрос",
            status_code=400,
        )
    if kind == "fertilizer" and waiting_days is not None:
        raise AppError(
            "waiting_days задаётся только для СЗР (kind=ppp); у удобрений срока "
            "ожидания нет (docs/03 §1.4)",
            code="invalid_kind_fields",
            title="Некорректный запрос",
            status_code=400,
        )
    if kind == "ppp" and npk_content is not None:
        raise AppError(
            "npk_content задаётся только для удобрений (kind=fertilizer)",
            code="invalid_kind_fields",
            title="Некорректный запрос",
            status_code=400,
        )
    if kind == "fertilizer" and active_substance is not None:
        raise AppError(
            "active_substance задаётся только для СЗР (kind=ppp)",
            code="invalid_kind_fields",
            title="Некорректный запрос",
            status_code=400,
        )
    duplicate = db.scalars(
        select(AgroInput).where(AgroInput.kind == kind, AgroInput.name == name)
    ).first()
    if duplicate is not None:
        raise AppError(
            f"Препарат «{name}» (kind={kind}) уже есть в справочнике",
            code="duplicate_input",
            title="Конфликт состояния",
            status_code=409,
        )
    n_pct, p2o5_pct, k2o_pct = (
        (npk_content + [None, None, None])[:3] if npk_content else (None, None, None)
    )
    item = AgroInput(
        kind=kind,
        name=name,
        n_pct=n_pct,
        p2o5_pct=p2o5_pct,
        k2o_pct=k2o_pct,
        active_substance=active_substance,
        concentration=concentration,
        unit=unit,
        waiting_days=waiting_days,
        approved_crops=approved_crops,
        max_treatments_per_season=max_treatments_per_season,
        restrictions=restrictions,
    )
    db.add(item)
    db.flush()  # id для аудита
    _audit(
        db,
        user=user,
        entity="agro_input",
        entity_id=item.id,
        action="create",
        changed={"kind": kind, "name": name, "unit": unit},
    )
    try:
        db.commit()
    except IntegrityError as exc:  # гонка дубля name+kind (uq_dat_agro_input_kind_name)
        db.rollback()
        if "uq_dat_agro_input_kind_name" not in str(exc):
            raise
        raise AppError(
            f"Препарат «{name}» (kind={kind}) уже есть в справочнике",
            code="duplicate_input",
            title="Конфликт состояния",
            status_code=409,
        ) from exc
    db.refresh(item)
    return item


def list_inputs(
    db: Session,
    *,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AgroInput], int]:
    """API-14: GET /agro-inputs — фильтры kind и подстрока имени q."""
    filters = []
    if kind is not None:
        if kind not in INPUT_KINDS:
            raise AppError(
                f"Неизвестный kind препарата «{kind}» (допустимо: {', '.join(INPUT_KINDS)})",
                code="invalid_kind",
                title="Некорректный запрос",
                status_code=400,
            )
        filters.append(AgroInput.kind == kind)
    if q and q.strip():
        filters.append(AgroInput.name.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(AgroInput).where(*filters))
    items = db.scalars(
        select(AgroInput)
        .where(*filters)
        .order_by(AgroInput.kind, AgroInput.name)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), int(total or 0)


#: планы внесения в единой операции (§7.10): типы из API-15
TREATMENT_OP_TYPES = ("spraying", "fertilizing", "seed_treatment")


def _season_plans(db: Session, field_id: uuid.UUID, year: int) -> list[FieldOperation]:
    """Не отменённые плановые внесения сезона из единой операции (§7.10
    адаптер): операции с расходником; база RULE-AGRO-07/10."""
    return list(
        db.scalars(
            select(FieldOperation)
            .join(CropCycle, FieldOperation.cycle_id == CropCycle.id)
            .where(
                CropCycle.field_id == field_id,
                CropCycle.year == year,
                FieldOperation.status != "cancelled",
                FieldOperation.input_id.isnot(None),
                FieldOperation.operation_type.in_(TREATMENT_OP_TYPES),
                FieldOperation.planned_date.isnot(None),
            )
        ).all()
    )


def treatment_plan_status(op: FieldOperation) -> str:
    """Статус плана-внесения в терминах API-15 (легаси) из операции §7.10."""
    if op.status == "cancelled":
        return "cancelled"
    if op.fact_date is not None:
        return "completed"
    if op.planned_date is None:
        return "draft"
    return "approved"


def validate_treatment_plan(
    db: Session,
    *,
    cycle: CropCycle,
    item: AgroInput,
    dose: float,
    operation_type: str,
    planned_date,
    exclude_plan_id: uuid.UUID | None = None,
) -> None:
    """Проверки RULE-AGRO-07/08/10 для нового или согласуемого плана внесения.

    exclude_plan_id — сам согласуемый план (не считается в «уже запланировано»).
    Все нарушения — 422 rule_violation с extra (RFC 7807, docs/06 §5).
    """
    if operation_type not in OPERATION_TYPES:
        raise AppError(
            "Неизвестный operation_type «{op}» (допустимо: {ops})".format(
                op=operation_type, ops=", ".join(OPERATION_TYPES)
            ),
            code="invalid_operation_type",
            title="Некорректный запрос",
            status_code=400,
        )
    culture = db.get_one(Culture, cycle.culture_id)

    # RULE-AGRO-07: доза ≥ 0
    if v := rules.check_dose_non_negative(dose=dose, input_name=item.name):
        raise _rule_violation(v)

    # RULE-AGRO-10: допустимые культуры препарата
    if v := rules.check_approved_crops(
        approved_crops=list(item.approved_crops or []),
        culture_id=cycle.culture_id,
        input_name=item.name,
        culture_name=culture.name,
    ):
        raise _rule_violation(v)

    # RULE-AGRO-08: срок ожидания до плановой уборки цикла (только spraying)
    if operation_type == "spraying":
        if v := rules.check_waiting_days(
            planned_date=planned_date,
            harvest_date=cycle.planned_harvest_date,
            waiting_days=item.waiting_days,
            input_name=item.name,
            culture_name=culture.name,
        ):
            raise _rule_violation(v)

    season = [p for p in _season_plans(db, cycle.field_id, cycle.year) if p.id != exclude_plan_id]
    inputs_by_id = {
        i.id: i
        for i in db.scalars(
            select(AgroInput).where(AgroInput.id.in_([p.input_id for p in season]))
        ).all()
    }

    # RULE-AGRO-07: суммарный N за сезон ≤ 180 кг д.в./га (default, v1)
    current_n = sum(
        rules.nitrogen_kg_dv_ha(
            dose=float(p.input_dose or 0.0),
            unit=inputs_by_id[p.input_id].unit,
            n_pct=inputs_by_id[p.input_id].n_pct,
        )
        for p in season
    )
    new_n = rules.nitrogen_kg_dv_ha(dose=dose, unit=item.unit, n_pct=item.n_pct)
    if v := rules.check_n_season(current_n=current_n, new_n=new_n, input_name=item.name):
        raise _rule_violation(v)

    # RULE-AGRO-10: обработок одним действующим веществом ≤ max_treatments
    if item.active_substance is not None:
        count = sum(
            1 for p in season if inputs_by_id[p.input_id].active_substance == item.active_substance
        )
        if v := rules.check_max_treatments(
            count=count,
            max_treatments=item.max_treatments_per_season,
            substance=item.active_substance,
            input_name=item.name,
        ):
            raise _rule_violation(v)


def create_treatment_plan(
    db: Session,
    *,
    user: User,
    cycle_id: uuid.UUID,
    input_id: uuid.UUID,
    dose: float,
    operation_type: str,
    planned_date,
    planned_phase: str | None = None,
    basis_recommendation_id: uuid.UUID | None = None,
) -> FieldOperation:
    """API-15 (адаптер §7.10): плановая операция внесения с расходником.

    Создаётся через единый сервис операций (source='plan_execution' —
    признак происхождения из API-15); RULE-AGRO-07/08/10 без изменений.
    """
    cycle = get_cycle(db, cycle_id)
    item = get_input(db, input_id)
    validate_treatment_plan(
        db,
        cycle=cycle,
        item=item,
        dose=dose,
        operation_type=operation_type,
        planned_date=planned_date,
    )
    from app.modules.operations.schemas import UnifiedOperationPlanIn
    from app.modules.operations.service import create_plan

    operation = create_plan(
        db,
        data=UnifiedOperationPlanIn(
            field_id=cycle.field_id,
            cycle_id=cycle_id,
            operation_type=operation_type,
            planned_date=planned_date,
            input_id=input_id,
            input_dose=dose,
            basis_recommendation_id=basis_recommendation_id,
        ),
    )
    # пометить происхождение адаптером (план из API-15)
    operation.source = "plan_execution"
    _audit(
        db,
        user=user,
        entity="treatment_plan",
        entity_id=operation.id,
        action="create",
        changed={
            "cycle_id": str(cycle_id),
            "input_id": str(input_id),
            "dose": dose,
            "operation_type": operation_type,
        },
    )
    db.commit()
    db.refresh(operation)
    return operation


def list_treatment_plans(
    db: Session,
    *,
    status: str | None = None,
    cycle_id: uuid.UUID | None = None,
    field_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple], int]:
    """API-15 (адаптер §7.10): плановые внесения из единой операции.

    Строки (операция, цикл, поле, культура, препарат); статус — легаси-мэппинг
    treatment_plan_status (done→completed, план с датой→approved и т.д.).
    """
    filters = [
        FieldOperation.input_id.isnot(None),
        FieldOperation.operation_type.in_(TREATMENT_OP_TYPES),
    ]
    if status is not None:
        if status not in TREATMENT_STATUSES:
            raise AppError(
                "Неизвестный статус плана «{s}» (допустимо: {sts})".format(
                    s=status, sts=", ".join(TREATMENT_STATUSES)
                ),
                code="invalid_status",
                title="Некорректный запрос",
                status_code=400,
            )
        if status == "cancelled":
            filters.append(FieldOperation.status == "cancelled")
        elif status == "completed":
            filters.append(FieldOperation.fact_date.isnot(None))
        elif status == "draft":
            filters.append(FieldOperation.planned_date.is_(None))
        else:  # approved / in_progress — плановая операция с датой, без факта
            filters.append(FieldOperation.planned_date.isnot(None))
            filters.append(FieldOperation.fact_date.is_(None))
    if cycle_id is not None:
        filters.append(FieldOperation.cycle_id == cycle_id)
    if field_id is not None:
        filters.append(CropCycle.field_id == field_id)
    total = db.scalar(
        select(func.count())
        .select_from(FieldOperation)
        .join(CropCycle, FieldOperation.cycle_id == CropCycle.id)
        .where(*filters)
    )
    rows = db.execute(
        select(FieldOperation, CropCycle, Field, Culture, AgroInput)
        .join(CropCycle, FieldOperation.cycle_id == CropCycle.id)
        .join(Field, CropCycle.field_id == Field.id)
        .join(Culture, CropCycle.culture_id == Culture.id)
        .join(AgroInput, FieldOperation.input_id == AgroInput.id)
        .where(*filters)
        .order_by(FieldOperation.created_at.desc(), FieldOperation.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [tuple(r) for r in rows], int(total or 0)


def approve_treatment_plan(db: Session, *, user: User, plan: FieldOperation) -> FieldOperation:
    """API-16 (адаптер §7.10): согласование плана-внесения.

    В единой модели плановая операция с датой уже действует (легаси
    «approved»), поэтому смена статуса не нужна: перепроверяем правила
    RULE-AGRO-07/08/10 (за время жизни плана лимиты могли занять другие),
    пишем аудит. Выполненное (fact_date) согласовывать нельзя — 409.
    """
    if plan.fact_date is not None or plan.status == "cancelled":
        raise AppError(
            "Согласовать можно только действующий план "
            f"(текущий статус: {treatment_plan_status(plan)})",
            code="invalid_state",
            title="Конфликт состояния",
            status_code=409,
        )
    cycle = db.get_one(CropCycle, plan.cycle_id)
    item = get_input(db, plan.input_id)
    validate_treatment_plan(
        db,
        cycle=cycle,
        item=item,
        dose=float(plan.input_dose or 0.0),
        operation_type=plan.operation_type,
        planned_date=plan.planned_date,
        exclude_plan_id=plan.id,
    )
    _audit(
        db,
        user=user,
        entity="treatment_plan",
        entity_id=plan.id,
        action="status_change",
        changed={"status_from": "draft", "status_to": "approved"},
    )
    db.commit()
    db.refresh(plan)
    return plan
