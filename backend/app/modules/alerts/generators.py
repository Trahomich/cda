"""Генераторы алертов (MVP-12, ADR-04: правила на нормализованных данных).

Вход — доменные таблицы dat_forecast / dat_satellite_scene (после ingest);
выход — строки dat_alert. Генераторы идемпотентны за счёт дедупликации
«активный алерт того же type + field_id не дублируется».

Запуск:
- по событиям шины (ADR-01): scene.ingested → проверка NDVI-падения и
  паузы, forecast.ingested → погодная проверка (см. engine.py);
- периодическим прогоном планировщика (alerts scheduler) — страховка
  от пропущенных событий.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agronomy import Alert, CropCycle, Field
from app.models.integrations import Forecast, SatelliteScene

logger = logging.getLogger(__name__)

ALERT_TYPES = frozenset(
    {"weather_danger", "ndvi_anomaly", "deviation", "machine_service", "data_source"}
)
SEVERITIES = frozenset({"info", "warning", "critical"})

# порядок критичности погодных явлений (docs/03 §1.8 hazard)
_HAZARD_SEVERITY = {
    "frost": "critical",
    "hail": "critical",
    "heavy_rain": "warning",
    "wind": "warning",
}
_HAZARD_TITLE = {
    "frost": "заморозки",
    "hail": "град",
    "heavy_rain": "ливень",
    "wind": "сильный ветер",
}

# активные циклы: сезон ещё идёт (planned/in_progress, completed исключён)
ACTIVE_CYCLE_STATUSES = ("planned", "in_progress")


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """Итог прогона генераторов: сколько алертов создано и пропущено дубликатов."""

    created: int
    duplicates: int


class AlertGenerators:
    """Простые правила-генераторы MVP (погода, NDVI-падение, пауза сцен)."""

    def __init__(
        self,
        *,
        ndvi_drop_pct: float = 15.0,
        ndvi_window_days: int = 10,
        satellite_stale_days: int = 14,
        forecast_horizon_days: int = 7,
    ) -> None:
        self._ndvi_drop_pct = ndvi_drop_pct
        self._ndvi_window_days = ndvi_window_days
        self._satellite_stale_days = satellite_stale_days
        self._forecast_horizon_days = forecast_horizon_days

    # -- общее --------------------------------------------------------------

    @staticmethod
    def active_fields_with_cycles(db: Session) -> dict[uuid.UUID, str]:
        """Активные поля с активными циклами: {field_id: name}."""
        rows = db.execute(
            select(Field.id, Field.name)
            .join(CropCycle, CropCycle.field_id == Field.id)
            .where(Field.status == "active", CropCycle.status.in_(ACTIVE_CYCLE_STATUSES))
            .distinct()
        ).fetchall()
        return {row.id: row.name for row in rows}

    @staticmethod
    def create_alert(
        db: Session,
        *,
        type: str,  # noqa: A002 — имя атрибута ENT-ALERT
        severity: str,
        field_id: uuid.UUID,
        payload: dict,
    ) -> Alert | None:
        """Создать алерт с дедупликацией: активный того же type+field не дублируется.

        Возвращает созданный алерт или None (дубль активного алерта).
        """
        duplicate = db.scalar(
            select(Alert.id).where(
                Alert.type == type,
                Alert.field_id == field_id,
                Alert.status != "closed",
            )
        )
        if duplicate is not None:
            return None
        alert = Alert(type=type, severity=severity, field_id=field_id, payload=payload)
        db.add(alert)
        db.commit()
        return alert

    # -- погода: неблагоприятное явление в прогнозе ---------------------------

    def check_weather(self, db: Session) -> GenerationReport:
        """Погодные алерты по dat_forecast.hazard на поля с активными циклами."""
        fields = self.active_fields_with_cycles(db)
        if not fields:
            return GenerationReport(created=0, duplicates=0)
        today = date.today()
        rows = db.execute(
            select(Forecast)
            .where(
                Forecast.field_id.in_(fields),
                Forecast.hazard != "none",
                Forecast.forecast_for >= today,
                Forecast.forecast_for <= today + timedelta(days=self._forecast_horizon_days),
            )
            .order_by(Forecast.field_id, Forecast.forecast_for)
        ).fetchall()
        by_field: dict[uuid.UUID, list[Forecast]] = {}
        for row in rows:
            by_field.setdefault(row.field_id, []).append(row)
        created = duplicates = 0
        for field_id, forecasts in by_field.items():
            worst = max(forecasts, key=lambda f: (f.hazard == "frost", f.hazard == "hail"))
            severity = _HAZARD_SEVERITY[worst.hazard]
            day_refs = [
                {
                    "forecast_for": str(f.forecast_for),
                    "hazard": f.hazard,
                    "temperature_min_c": float(f.temperature_min_c)
                    if f.temperature_min_c is not None
                    else None,
                    "precipitation_mm": float(f.precipitation_mm)
                    if f.precipitation_mm is not None
                    else None,
                }
                for f in forecasts
            ]
            alert = self.create_alert(
                db,
                type="weather_danger",
                severity=severity,
                field_id=field_id,
                payload={
                    "message": (
                        f"Неблагоприятная погода на поле «{fields[field_id]}»: "
                        f"{_HAZARD_TITLE[worst.hazard]} "
                        f"с {min(f.forecast_for for f in forecasts)}"
                    ),
                    "rule_id": "RULE-AGRO-09",
                    "hazard": worst.hazard,
                    "days": day_refs,
                    "data_refs": [
                        {"entity": "forecast", "id": str(f.id)} for f in forecasts
                    ],
                },
            )
            if alert is None:
                duplicates += 1
            else:
                created += 1
                logger.info(
                    "алерт weather_danger (%s) создан: поле %s", worst.hazard, field_id
                )
        return GenerationReport(created=created, duplicates=duplicates)

    # -- NDVI: существенное падение между сценами ------------------------------

    def check_ndvi(self, db: Session, field_id: uuid.UUID | None = None) -> GenerationReport:
        """Алерт ndvi_anomaly: mean NDVI упал более чем на ndvi_drop_pct между сценами.

        Сравниваются две последние сцены ndvi поля, если предыдущая не старше
        ndvi_window_days дней относительно новой (только реальные соседние сцены).
        """
        if field_id is not None:
            fields: dict[uuid.UUID, str] = {field_id: ""}
        else:
            rows = db.execute(
                select(Field.id).where(Field.status == "active")
            ).fetchall()
            fields = {row.id: "" for row in rows}
        created = duplicates = 0
        for fid in fields:
            scenes = db.execute(
                select(SatelliteScene)
                .where(SatelliteScene.field_id == fid, SatelliteScene.index == "ndvi")
                .order_by(SatelliteScene.captured_at.desc())
                .limit(2)
            ).fetchall()
            if len(scenes) < 2:
                continue
            latest, prev = scenes[0], scenes[1]
            gap = latest.captured_at.date() - prev.captured_at.date()
            if gap > timedelta(days=self._ndvi_window_days) or gap <= timedelta(0):
                continue
            prev_avg = float(prev.index_avg)
            latest_avg = float(latest.index_avg)
            if prev_avg <= 0:
                continue
            drop_pct = (prev_avg - latest_avg) / prev_avg * 100.0
            if drop_pct <= self._ndvi_drop_pct:
                continue
            field_name = fields[fid] or db.scalar(select(Field.name).where(Field.id == fid))
            alert = self.create_alert(
                db,
                type="ndvi_anomaly",
                severity="warning",
                field_id=fid,
                payload={
                    "message": (
                        f"Падение NDVI на поле «{field_name}» на {drop_pct:.1f} % "
                        f"({prev_avg:.3f} → {latest_avg:.3f}) за {gap.days} дн. — "
                        "кандидат на обследование"
                    ),
                    "rule_id": "REQ-F-029",
                    "prev_avg": prev_avg,
                    "latest_avg": latest_avg,
                    "drop_pct": round(drop_pct, 2),
                    "prev_captured_at": prev.captured_at.isoformat(),
                    "latest_captured_at": latest.captured_at.isoformat(),
                    "cloud_pct": float(latest.cloud_pct),
                    "data_refs": [
                        {"entity": "satellite_scene", "id": str(prev.id)},
                        {"entity": "satellite_scene", "id": str(latest.id)},
                    ],
                },
            )
            if alert is None:
                duplicates += 1
            else:
                created += 1
                logger.info("алерт ndvi_anomaly создан: поле %s (−%.1f%%)", fid, drop_pct)
        return GenerationReport(created=created, duplicates=duplicates)

    # -- пауза спутниковых данных ---------------------------------------------

    def check_satellite_gap(self, db: Session) -> GenerationReport:
        """Алерт data_source: нет свежих сцен на поле stale_days суток и дольше."""
        stale_before = datetime.now(tz=UTC) - timedelta(days=self._satellite_stale_days)
        rows = db.execute(
            select(Field.id, Field.name)
            .where(Field.status == "active")
            .order_by(Field.name)
        ).fetchall()
        created = duplicates = 0
        for field_id, name in rows:
            latest = db.execute(
                select(SatelliteScene.captured_at, SatelliteScene.id)
                .where(SatelliteScene.field_id == field_id)
                .order_by(SatelliteScene.captured_at.desc())
                .limit(1)
            ).first()
            latest_at = latest[0] if latest else None
            latest_id = latest[1] if latest else None
            if latest_at is not None and latest_at > stale_before:
                continue
            last_str = latest_at.isoformat() if latest_at is not None else None
            alert = self.create_alert(
                db,
                type="data_source",
                severity="info",
                field_id=field_id,
                payload={
                    "message": (
                        f"Нет свежих спутниковых сцен по полю «{name}» более "
                        f"{self._satellite_stale_days} сут."
                        + (f" Последняя сцена: {last_str}." if last_str else "")
                    ),
                    "rule_id": "MVP-12/satellite_gap",
                    "last_captured_at": last_str,
                    "data_refs": (
                        [{"entity": "satellite_scene", "id": str(latest_id)}]
                        if latest_id
                        else []
                    ),
                },
            )
            if alert is None:
                duplicates += 1
            else:
                created += 1
                logger.info("алерт data_source создан: поле %s", field_id)
        return GenerationReport(created=created, duplicates=duplicates)

    def check_all(self, db: Session) -> dict[str, GenerationReport]:
        """Полный прогон всех генераторов (планировщик, страховка от пропуска событий)."""
        return {
            "weather": self.check_weather(db),
            "ndvi": self.check_ndvi(db),
            "satellite_gap": self.check_satellite_gap(db),
        }


__all__ = [
    "ACTIVE_CYCLE_STATUSES",
    "ALERT_TYPES",
    "AlertGenerators",
    "GenerationReport",
    "SEVERITIES",
]
