"""Модели техники (задача MVP-2, docs/06-data-api.md §2, docs/04 §1).

Схема создаётся миграцией 0005_machinery; модели — зеркало миграции.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Machine(Base):
    """Единица техники; госномер уникален, где задан (partial UQ)."""

    __tablename__ = "dat_machine"
    __table_args__ = (
        CheckConstraint(
            "machine_type IN ('tractor', 'combine', 'sprayer', 'spreader', "
            "'seeder', 'truck', 'loader', 'other')",
            name="ck_dat_machine_type",
        ),
        CheckConstraint(
            "status IN ('active', 'maintenance', 'retired')",
            name="ck_dat_machine_status",
        ),
        Index(
            "uq_dat_machine_gov_number",
            "gov_number",
            unique=True,
            postgresql_where="gov_number IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    machine_type: Mapped[str] = mapped_column(String(15), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    gov_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    width_m: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    tank_capacity_l: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engine_hours: Mapped[float] = mapped_column(Numeric(10, 1), nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(15), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MachineJob(Base):
    """Работа техники: draft из телематики подтверждает человек (RULE-TECH-04)."""

    __tablename__ = "dat_machine_job"
    __table_args__ = (
        CheckConstraint("area_ha >= 0", name="ck_dat_machine_job_area"),
        CheckConstraint(
            "status IN ('draft', 'confirmed', 'rejected')",
            name="ck_dat_machine_job_status",
        ),
        CheckConstraint("source IN ('manual', 'telemetry')", name="ck_dat_machine_job_source"),
        Index("ix_dat_machine_job_machine_date", "machine_id", "performed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_machine.id", name="fk_dat_machine_job_machine"), nullable=False
    )
    field_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("dat_field_operation.id", name="fk_dat_machine_job_field_operation"),
        nullable=True,
    )
    performed_at: Mapped[date] = mapped_column(Date, nullable=False)
    area_ha: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    fuel_l: Mapped[float | None] = mapped_column(Numeric(8, 1), nullable=True)
    track_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_h: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="draft")
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Maintenance(Base):
    """ТО и ремонт (ENT-MAINTENANCE, 04 §1); алерты RULE-TECH-02/05 — вне MVP-7."""

    __tablename__ = "dat_maintenance"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('service', 'repair', 'inspection')", name="ck_dat_maintenance_kind"
        ),
        CheckConstraint("due_at_hours >= 0", name="ck_dat_maintenance_due_hours"),
        CheckConstraint("done_at_hours >= 0", name="ck_dat_maintenance_done_hours"),
        CheckConstraint("downtime_h >= 0", name="ck_dat_maintenance_downtime"),
        Index("ix_dat_maintenance_machine_due", "machine_id", "due_at_hours"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_machine.id", name="fk_dat_maintenance_machine"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(15), nullable=False)
    due_at_hours: Mapped[float] = mapped_column(Numeric(10, 1), nullable=False)
    done_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    done_at_hours: Mapped[float | None] = mapped_column(Numeric(10, 1), nullable=True)
    downtime_h: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OperationMachine(Base):
    """Техника единой операции, 1:N (§7.10): агрегат + прицеп + механизатор."""

    __tablename__ = "dat_operation_machine"
    __table_args__ = (
        CheckConstraint("hours IS NULL OR hours >= 0", name="ck_dat_operation_machine_hours"),
        Index("ix_dat_operation_machine_operation", "operation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("dat_field_operation.id", name="fk_dat_operation_machine_operation"),
        nullable=False,
    )
    machine_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_machine.id", name="fk_dat_operation_machine_machine"), nullable=False
    )
    trailer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("dat_machine.id", name="fk_dat_operation_machine_trailer"), nullable=True
    )
    operator: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hours: Mapped[float | None] = mapped_column(Numeric(8, 1), nullable=True)


__all__ = ["Machine", "MachineJob", "Maintenance", "OperationMachine"]
