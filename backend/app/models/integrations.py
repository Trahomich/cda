"""Модели погоды, спутника и журнала источников (MVP-2, 06 §2, 05 §3).

Схема создаётся миграцией 0006_integrations; модели — зеркало миграции.
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
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WeatherObservation(Base):
    """Факт погоды (сутки × поле); дедупликация UQ(field_id, observed_at, source_id)."""

    __tablename__ = "dat_weather_observation"
    __table_args__ = (
        UniqueConstraint("field_id", "observed_at", "source_id", name="uq_dat_weather_observation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("dat_field.id", name="fk_dat_weather_observation_field"),
        nullable=False,
    )
    observed_at: Mapped[date] = mapped_column(Date, nullable=False)
    temperature_avg_c: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    temperature_min_c: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    temperature_max_c: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    precipitation_mm: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    wind_max_ms: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    humidity_avg_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    source_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("ref_source.source_id", name="fk_dat_weather_observation_source"),
        nullable=False,
    )
    trust: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Forecast(Base):
    """Прогноз на сутки; хранится последняя версия (06 §4)."""

    __tablename__ = "dat_forecast"
    __table_args__ = (
        CheckConstraint(
            "hazard IN ('none', 'frost', 'heavy_rain', 'hail', 'wind')",
            name="ck_dat_forecast_hazard",
        ),
        UniqueConstraint("field_id", "forecast_for", name="uq_dat_forecast"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_forecast_field"), nullable=False
    )
    forecast_for: Mapped[date] = mapped_column(Date, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature_min_c: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    temperature_max_c: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    precipitation_mm: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    wind_max_ms: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    humidity_avg_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    hazard: Mapped[str] = mapped_column(String(15), nullable=False, server_default="none")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SatelliteScene(Base):
    """Индекс × поле × дата; cloud_pct ≤ 60 (05 §5)."""

    __tablename__ = "dat_satellite_scene"
    __table_args__ = (
        CheckConstraint(
            "\"index\" IN ('ndvi', 'ndre', 'ndmi', 'savi')",
            name="ck_dat_satellite_scene_index",
        ),
        CheckConstraint("cloud_pct <= 60", name="ck_dat_satellite_scene_cloud"),
        UniqueConstraint(
            "field_id", "index", "captured_at", "source_id", name="uq_dat_satellite_scene"
        ),
        Index("ix_dat_satellite_scene_field_date", "field_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_satellite_scene_field"), nullable=False
    )
    index: Mapped[str] = mapped_column(String(5), nullable=False)
    captured_at: Mapped[date] = mapped_column(Date, nullable=False)
    resolution_m: Mapped[int] = mapped_column(Integer, nullable=False)
    index_avg: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    index_min: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    index_max: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    anomaly_zones_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cloud_pct: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)
    composite_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    source_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("ref_source.source_id", name="fk_dat_satellite_scene_source"),
        nullable=False,
    )
    trust: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SatelliteCatalogItem(Base):
    """Каталогизированная сцена Sentinel-2 (v1-04, #50): метаданные STAC-поиска.

    Отдельная сущность от dat_satellite_scene: каталогизируем сцену при
    нахождении в STAC (item_id, tile, cloud, ссылки COG), статистику индексов
    считает этап NDVI (#51) и пишет в dat_satellite_scene по правилам 05 §5.
    Статусы: cataloged (ждёт расчёта) → processed (индексы рассчитаны) /
    failed (ошибка расчёта, повтор в следующем цикле планировщика).
    """

    __tablename__ = "dat_satellite_catalog"
    __table_args__ = (
        CheckConstraint(
            "status IN ('cataloged', 'processed', 'failed')",
            name="ck_dat_satellite_catalog_status",
        ),
        CheckConstraint("cloud_pct <= 60", name="ck_dat_satellite_catalog_cloud"),
        CheckConstraint("cloud_pct >= 0", name="ck_dat_satellite_catalog_cloud_pos"),
        UniqueConstraint("field_id", "item_id", name="uq_dat_satellite_catalog"),
        Index("ix_dat_satellite_catalog_status", "status", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_dat_satellite_catalog_field"), nullable=False
    )
    captured_at: Mapped[date] = mapped_column(Date, nullable=False)
    tile_id: Mapped[str] = mapped_column(String(20), nullable=False)
    cloud_pct: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)
    assets: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="cataloged")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SourceJournalEntry(Base):
    """Событие состояния адаптера: ok/degraded/error + причина (REQ-F-034)."""

    __tablename__ = "dat_source_journal"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'degraded', 'error')", name="ck_dat_source_journal_status"
        ),
        Index("ix_dat_source_journal_source_at", "source_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("ref_source.source_id", name="fk_dat_source_journal_source"),
        nullable=False,
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Forecast",
    "SatelliteCatalogItem",
    "SatelliteScene",
    "SourceJournalEntry",
    "WeatherObservation",
]
