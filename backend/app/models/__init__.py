"""Пакет ORM-моделей ЦДА. Модули добавляют таблицы по мере реализации MVP."""

from app.models.agronomy import (
    AgroInput,
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    FieldVersion,
    Recommendation,
    RotationPlan,
    TreatmentPlan,
    YieldRecord,
)
from app.models.ai import AIFieldAdvice
from app.models.auth import AuditRecord, User
from app.models.integrations import (
    Forecast,
    SatelliteScene,
    SourceJournalEntry,
    WeatherObservation,
)
from app.models.machinery import Machine, MachineJob, OperationMachine
from app.models.references import (
    Agrochem,
    Culture,
    PredecessorRule,
    Price,
    Source,
    WeatherRule,
)

__all__ = [
    "AIFieldAdvice",
    "Agrochem",
    "AgroInput",
    "Alert",
    "AuditRecord",
    "CropCycle",
    "Culture",
    "Field",
    "FieldOperation",
    "FieldVersion",
    "Forecast",
    "Machine",
    "MachineJob",
    "OperationMachine",
    "PredecessorRule",
    "Price",
    "Recommendation",
    "RotationPlan",
    "SatelliteScene",
    "Source",
    "SourceJournalEntry",
    "TreatmentPlan",
    "User",
    "WeatherObservation",
    "WeatherRule",
    "YieldRecord",
]
