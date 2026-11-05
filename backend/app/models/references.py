"""Модели справочников ref_ (задача MVP-2, docs/06-data-api.md §3).

Схема создаётся миграцией 0003_references; модели — зеркало миграции
(проверяется alembic check / autogenerate без diff).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Culture(Base):
    """Культура (справочник севооборота, RULE-AGRO-01/03)."""

    __tablename__ = "ref_culture"
    __table_args__ = (
        CheckConstraint("kind IN ('main', 'forage', 'catch')", name="ck_ref_culture_kind"),
        UniqueConstraint("name", name="uq_ref_culture_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    forage_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    crop_group: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PredecessorRule(Base):
    """Матрица совместимости «предшественник → культура» (RULE-AGRO-01)."""

    __tablename__ = "ref_predecessor_matrix"
    __table_args__ = (
        CheckConstraint("verdict IN ('ok', 'warn', 'ban')", name="ck_ref_pred_matrix_verdict"),
        UniqueConstraint("culture_id", "predecessor_id", name="uq_ref_pred_matrix_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    culture_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ref_culture.id", name="fk_ref_pred_matrix_culture"), nullable=False
    )
    predecessor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("ref_culture.id", name="fk_ref_pred_matrix_predecessor"),
        nullable=False,
    )
    verdict: Mapped[str] = mapped_column(String(10), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Source(Base):
    """Адаптер источника данных; текущий статус, история — dat_source_journal."""

    __tablename__ = "ref_source"
    __table_args__ = (
        CheckConstraint(
            "last_status IN ('ok', 'degraded', 'error') OR last_status IS NULL",
            name="ck_ref_source_last_status",
        ),
        UniqueConstraint("source_id", name="uq_ref_source_source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WeatherRule(Base):
    """Параметры погодного окна по типу операции (RULE-AGRO-09)."""

    __tablename__ = "ref_weather_rule"
    __table_args__ = (UniqueConstraint("operation_type", name="uq_ref_weather_rule_op_type"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rule_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Agrochem(Base):
    """Результат агрохимического обследования поля (REQ-F-004, 031)."""

    __tablename__ = "ref_agrochem"
    __table_args__ = (Index("ix_ref_agrochem_field_date", "field_id", "sample_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_ref_agrochem_field"), nullable=False
    )
    sample_date: Mapped[date] = mapped_column(Date, nullable=False)
    ph: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    humus_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    n_mg100: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    p2o5_mg100: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    k2o_mg100: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Price(Base):
    """Цена культуры или препарата (REQ-F-033)."""

    __tablename__ = "ref_price"
    __table_args__ = (
        CheckConstraint("item_type IN ('crop', 'input')", name="ck_ref_price_item_type"),
        Index("ix_ref_price_item_date", "item_type", "item_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_type: Mapped[str] = mapped_column(String(10), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    price_rub: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Agrochem",
    "Culture",
    "PredecessorRule",
    "Price",
    "Source",
    "WeatherRule",
]
