"""Модели домена агрономии (задача MVP-2, docs/06-data-api.md §2, docs/03).

Схема создаётся миграцией 0004_agronomy; модели — зеркало миграции.
"""

import uuid
from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Геодезическая площадь контура в га (03 §6.1): ST_Area(geography) — м²
AREA_GA_COMPUTED = "round((ST_Area(contour) / 10000.0)::numeric, 2)"


class Field(Base):
    """Поле: контур, площадь (generated), текущая версия контура."""

    __tablename__ = "dat_field"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'merged', 'archived')", name="ck_dat_field_status"),
        # имя НЕ уникально: в реальном SHP-импорте имена повторяются (v1-02);
        # natural key импорта — external_id (docs/05 §IF-FIELDS)
        UniqueConstraint("external_id", name="uq_dat_field_external_id"),
        Index("ix_dat_field_contour", "contour", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    contour: Mapped[Geography] = mapped_column(
        # MultiPolygon: в реальном хозяйстве составные контуры (v1-02);
        # typmod MULTIPOLYGON принимает и одиночные полигоны
        Geography(geometry_type="MULTIPOLYGON", srid=4326),
        nullable=False,
    )
    area_ga: Mapped[float] = mapped_column(
        Numeric(10, 2), Computed(AREA_GA_COMPUTED, persisted=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="active")
    external_id: Mapped[str | None] = mapped_column(
        # natural key импорта полей: атрибут code SHP, при пустом — name
        String(100),
        nullable=True,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "dat_field_version.id", name="fk_dat_field_current_version", ondelete="SET NULL"
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FieldVersion(Base):
    """Версия контура поля (история, REQ-F-002)."""

    __tablename__ = "dat_field_version"
    __table_args__ = (UniqueConstraint("field_id", "version_no", name="uq_dat_field_version_no"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_field_version_field"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    contour: Mapped[Geography] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326), nullable=False
    )
    area_ga: Mapped[float] = mapped_column(
        Numeric(10, 2), Computed(AREA_GA_COMPUTED, persisted=True), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RotationPlan(Base):
    """План севооборота; активный план на диапазон лет один (partial UQ)."""

    __tablename__ = "dat_rotation_plan"
    __table_args__ = (
        CheckConstraint("year_to >= year_from", name="ck_dat_rotation_plan_years"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'archived')",
            name="ck_dat_rotation_plan_status",
        ),
        Index(
            "uq_dat_rotation_plan_active",
            "field_id",
            "year_from",
            "year_to",
            unique=True,
            postgresql_where="status <> 'archived'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_rotation_plan_field"), nullable=False
    )
    year_from: Mapped[int] = mapped_column(Integer, nullable=False)
    year_to: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="draft")
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CropCycle(Base):
    """Цикл культуры на поле за сезон (RULE-AGRO-03/04)."""

    __tablename__ = "dat_crop_cycle"
    __table_args__ = (
        CheckConstraint("kind IN ('main', 'catch', 'fallow')", name="ck_dat_crop_cycle_kind"),
        CheckConstraint(
            "status IN ('planned', 'in_progress', 'completed')",
            name="ck_dat_crop_cycle_status",
        ),
        Index(
            "uq_dat_crop_cycle_main",
            "field_id",
            "year",
            unique=True,
            postgresql_where="kind = 'main'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_crop_cycle_field"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    culture_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ref_culture.id", name="fk_dat_crop_cycle_culture"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    predecessor_cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("dat_crop_cycle.id", name="fk_dat_crop_cycle_predecessor"),
        nullable=True,
    )
    planned_yield_c_ha: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)
    planned_harvest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    out_of_rotation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, server_default="planned")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgroInput(Base):
    """Препарат: удобрение или СЗР (RULE-AGRO-07/08/10)."""

    __tablename__ = "dat_agro_input"
    __table_args__ = (
        CheckConstraint("kind IN ('fertilizer', 'ppp')", name="ck_dat_agro_input_kind"),
        CheckConstraint("unit IN ('kg_ha', 'l_ha', 'kg_dv_ha')", name="ck_dat_agro_input_unit"),
        CheckConstraint(
            "kind <> 'fertilizer' OR waiting_days IS NULL",
            name="ck_dat_agro_input_waiting_days",
        ),
        UniqueConstraint("kind", "name", name="uq_dat_agro_input_kind_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(15), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    n_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    p2o5_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    k2o_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    active_substance: Mapped[str | None] = mapped_column(String(200), nullable=True)
    concentration: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    unit: Mapped[str] = mapped_column(String(12), nullable=False)
    waiting_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_crops: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, server_default="{}"
    )
    max_treatments_per_season: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Recommendation(Base):
    """Рекомендация системы (RULE-AGRO-13: объяснимость обязательна)."""

    __tablename__ = "dat_recommendation"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('field', 'crop_cycle', 'treatment_plan')",
            name="ck_dat_recommendation_target_type",
        ),
        CheckConstraint(
            "status IN ('new', 'accepted', 'rejected', 'expired')",
            name="ck_dat_recommendation_status",
        ),
        Index("ix_dat_recommendation_status", "status"),
        Index("ix_dat_recommendation_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(500), nullable=False)
    rationale: Mapped[str] = mapped_column(String(1000), nullable=False)
    data_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="new")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TreatmentPlan(Base):
    """План внесения удобрений/СЗР; статусы — автомат 03 §2.1."""

    __tablename__ = "dat_treatment_plan"
    __table_args__ = (
        CheckConstraint("dose >= 0", name="ck_dat_treatment_plan_dose"),
        CheckConstraint(
            "operation_type IN ('spraying', 'fertilizing', 'seed_treatment')",
            name="ck_dat_treatment_plan_op_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved', 'in_progress', 'completed', 'cancelled')",
            name="ck_dat_treatment_plan_status",
        ),
        Index("ix_dat_treatment_plan_cycle_status", "cycle_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("dat_crop_cycle.id", name="fk_dat_treatment_plan_cycle"),
        nullable=False,
    )
    input_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("dat_agro_input.id", name="fk_dat_treatment_plan_input"),
        nullable=False,
    )
    dose: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_phase: Mapped[str | None] = mapped_column(String(100), nullable=True)
    basis_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("dat_recommendation.id", name="fk_dat_treatment_plan_basis"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(15), nullable=False, server_default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FieldOperation(Base):
    """Факт операции на поле (журнал работ, SC-03)."""

    __tablename__ = "dat_field_operation"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('spraying', 'fertilizing', 'seeding', "
            "'seed_treatment', 'harvesting', 'tillage', 'other')",
            name="ck_dat_field_operation_type",
        ),
        CheckConstraint(
            "source IN ('manual', 'telemetry_import', 'plan_execution')",
            name="ck_dat_field_operation_source",
        ),
        CheckConstraint(
            "status IN ('draft', 'planned', 'in_progress', 'done', 'cancelled')",
            name="ck_dat_field_operation_status",
        ),
        CheckConstraint(
            "cost_fact_mode IN ('unit', 'total') OR cost_fact_mode IS NULL",
            name="ck_dat_field_operation_cost_fact_mode",
        ),
        CheckConstraint(
            "cost_fact_value IS NULL OR cost_fact_value >= 0",
            name="ck_dat_field_operation_cost_fact",
        ),
        CheckConstraint(
            "cost_plan_mode IN ('unit', 'total') OR cost_plan_mode IS NULL",
            name="ck_dat_field_operation_cost_plan_mode",
        ),
        CheckConstraint(
            "cost_plan_value IS NULL OR cost_plan_value >= 0",
            name="ck_dat_field_operation_cost_plan",
        ),
        # §7.10: факт закрыт ⇒ done; план (planned_date есть, факта нет) ⇒
        # planned/in_progress (выполняемая); cancelled вне инварианта
        CheckConstraint(
            "status = 'cancelled' OR (fact_date IS NOT NULL AND status = 'done') "
            "OR (fact_date IS NULL AND status <> 'done')",
            name="ck_dat_field_operation_fact_status",
        ),
        CheckConstraint(
            "status = 'cancelled' OR fact_date IS NOT NULL "
            "OR planned_date IS NOT NULL OR status = 'draft'",
            name="ck_dat_field_operation_plan_status",
        ),
        Index("ix_dat_field_operation_field_date", "field_id", "date"),
        Index("ix_dat_field_operation_planned", "cycle_id", "planned_date"),
        Index("ix_dat_field_operation_fact", "cycle_id", "fact_date"),
        Index("ix_dat_field_operation_cycle", "cycle_id"),
        # Идемпотентность офлайн-очереди PWA (API-17): повторный POST с тем же
        # client_id не создаёт дубль (partial unique — client_id необязателен).
        Index(
            "uq_dat_field_operation_client_id",
            "client_id",
            unique=True,
            postgresql_where=text("client_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_field_operation_field"), nullable=False
    )
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("dat_crop_cycle.id", name="fk_dat_field_operation_cycle"),
        nullable=True,
    )
    operation_type: Mapped[str] = mapped_column(String(15), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    input_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("dat_agro_input.id", name="fk_dat_field_operation_input"),
        nullable=True,
    )
    actual_dose: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    actual_area_ga: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    performer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Идемпотентность офлайн-очереди PWA (API-17): uuid от клиента.
    client_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Ссылка на работу техники (docs/04; обратная связь field_operation_id).
    machine_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("dat_machine_job.id", name="fk_dat_field_operation_job"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    deviation_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Обоснование отклонения (RULE-AGRO-11): факт vs план.
    deviation_rationale: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # RULE-AGRO-12: площадь > 1,05 × площади поля — сохраняем, но помечаем
    # «требует проверки» (422 не возвращаем: факт важнее плана, 03 §3.4).
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String(15), nullable=False, server_default="draft")
    # Стоимость техработы (§7.9, #161): 'unit' — руб/га (× площадь операции),
    # 'total' — фикс за поле; NULL — стоимости нет (для факта — норматив
    # ref_operation_cost по типу операции)
    cost_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    cost_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class YieldRecord(Base):
    """Запись об урожайности."""

    __tablename__ = "dat_yield_record"
    __table_args__ = (
        CheckConstraint("yield_c_ha > 0", name="ck_dat_yield_record_value"),
        CheckConstraint(
            "source IN ('scales', 'combine_sensor', 'estimate')",
            name="ck_dat_yield_record_source",
        ),
        UniqueConstraint("cycle_id", "date", "source", name="uq_dat_yield_record"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_crop_cycle.id", name="fk_dat_yield_record_cycle"), nullable=False
    )
    yield_c_ha: Mapped[float] = mapped_column(Numeric(6, 1), nullable=False)
    gross_t: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Alert(Base):
    """Оповещение пользователя."""

    __tablename__ = "dat_alert"
    __table_args__ = (
        CheckConstraint(
            "type IN ('weather_danger', 'ndvi_anomaly', 'deviation', "
            "'machine_service', 'data_source')",
            name="ck_dat_alert_type",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="ck_dat_alert_severity"
        ),
        CheckConstraint("status IN ('new', 'read', 'closed')", name="ck_dat_alert_status"),
        Index("ix_dat_alert_status_user", "status", "user_id"),
        Index("ix_dat_alert_type", "type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    field_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_alert_field"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("dat_user.id", name="fk_dat_alert_user"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "AgroInput",
    "Alert",
    "CropCycle",
    "Field",
    "FieldOperation",
    "FieldVersion",
    "Recommendation",
    "RotationPlan",
    "TreatmentPlan",
    "YieldRecord",
]
