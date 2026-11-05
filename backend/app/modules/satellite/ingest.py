"""Ingest-слой модуля спутника (docs/05 §2/§4/§6; задача MVP-10).

Конвейер: каталог Sentinel-2 → сцены по контуру → NDVI-статистика →
dat_satellite_scene. Расписание — опрос каталога раз в 6 ч (05 §4);
при новой сцене публикуется событие scene.ingested во внутреннюю шину
(ADR-01) и пишется журнал dat_source_journal + ref_source.last_status.

Качество (05 §5):
- cloud_pct > 60 — не сохраняется вовсе (плюс CHECK в БД);
- дедупликация UQ(field_id, index, captured_at, source_id) — upsert,
  повторная загрузка не создаёт дублей (05 §4);
- NDVI вне [−1; 1] — DataQualityError адаптера, поле попадает в failed.
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import events
from app.integrations import Scene, SourceUnavailableError
from app.models.agronomy import Field
from app.models.integrations import SatelliteScene, SourceJournalEntry
from app.models.references import Source

logger = logging.getLogger(__name__)

SCENE_INGESTED_EVENT = "scene.ingested"
"""Событие шины: новая сцена сохранена (payload — выжимка ENT-SATELLITE-SCENE)."""


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Итог прогона: сколько сцен сохранено, полей обработано, ошибок."""

    ok: int  # сохранённых сцен
    skipped: int  # полей пропущено (нет контура / нет сцен за окно)
    failed: list[str]

    @property
    def status(self) -> str:
        """ok — все поля и без ошибок; degraded — часть упала/пропущена; error — всё."""
        if self.ok > 0 and not self.failed:
            return "ok"
        if self.ok > 0 or self.failed:
            return "degraded"
        return "degraded"  # полей/сцен нет — источник не виноват, но и успеха нет


def contour_lookup_factory(db: Session):
    """Контур поля как GeoJSON: field_id -> dict (WGS84) или None."""

    def lookup(field_id: str) -> dict | None:
        row = db.execute(
            text(
                "SELECT ST_AsGeoJSON(contour)::json FROM dat_field "
                "WHERE id = CAST(:fid AS uuid) AND status = 'active'"
            ),
            {"fid": field_id},
        ).fetchone()
        return None if row is None else row[0]

    return lookup


class SatelliteIngest:
    """Прогон приёма сцен по всем активным полям и запрошенным индексам."""

    def __init__(
        self,
        adapter,
        *,
        indices: tuple[str, ...] = ("ndvi",),
        lookback_days: int = 14,
        max_cloud_pct: int = 20,
        retry_attempts: int = 5,
        retry_base_seconds: float = 3600.0,
    ) -> None:
        self._adapter = adapter
        self._indices = indices
        self._lookback_days = lookback_days
        self._max_cloud_pct = max_cloud_pct
        self._retry_attempts = max(1, retry_attempts)
        self._retry_base_seconds = retry_base_seconds

    # -- общее --------------------------------------------------------------

    @staticmethod
    def active_fields(db: Session) -> list[tuple[str, str]]:
        rows = db.execute(
            select(Field.id, Field.name).where(Field.status == "active").order_by(Field.name)
        ).fetchall()
        return [(str(row[0]), row[1]) for row in rows]

    def _fetch_with_retries(self, fetch):
        """Ретраи только для SourceUnavailableError (05 §6); (result, error)."""
        delay = self._retry_base_seconds
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                return fetch(), None
            except SourceUnavailableError as exc:
                last_error = exc
                logger.warning(
                    "спутник: ретрай %d/%d после %s", attempt + 1, self._retry_attempts, exc
                )
                if attempt + 1 < self._retry_attempts and delay > 0:
                    time.sleep(min(delay, 30.0))  # retry_base_seconds=0 в тестах
                    delay *= 2
        return None, f"источник недоступен: {last_error}"

    @staticmethod
    def log_source(db: Session, source_id: str, status: str, reason: str | None) -> None:
        """Строка в dat_source_journal + ref_source.last_status/last_success_at."""
        db.add(
            SourceJournalEntry(
                source_id=source_id,
                at=datetime.now(tz=UTC),
                status=status,
                reason=reason[:500] if reason else None,
            )
        )
        db.execute(
            update(Source)
            .where(Source.source_id == source_id)
            .values(
                last_status=status,
                last_success_at=datetime.now(tz=UTC) if status == "ok" else Source.last_success_at,
            )
        )
        db.commit()

    # -- запись dat_satellite_scene ------------------------------------------

    @staticmethod
    def _existing_keys(db: Session, scenes: list[Scene]) -> set[tuple]:
        """Ключи UQ уже сохранённых сцен — чтобы публиковать только новые."""
        if not scenes:
            return set()
        rows = db.execute(
            select(
                SatelliteScene.field_id,
                SatelliteScene.index,
                SatelliteScene.captured_at,
                SatelliteScene.source_id,
            )
        ).fetchall()
        return set(rows)

    def save_scenes(self, db: Session, scenes: list[Scene]) -> int:
        """Upsert по UQ(field_id, index, captured_at, source_id); >60 % не пишем."""
        rows = [s for s in scenes if s.cloud_pct <= 60.0]
        if not rows:
            return 0
        existing = self._existing_keys(db, rows)
        statement = pg_insert(SatelliteScene).values(
            [
                {
                    "field_id": s.field_id,
                    "index": s.index,
                    "captured_at": s.captured_at,
                    "resolution_m": s.resolution_m,
                    "index_avg": s.index_avg,
                    "index_min": s.index_min,
                    "index_max": s.index_max,
                    "anomaly_zones_ref": s.anomaly_zones_ref,
                    "cloud_pct": s.cloud_pct,
                    "composite_days": s.composite_days,
                    "source_id": s.source_id,
                    "trust": s.trust,
                }
                for s in rows
            ]
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_dat_satellite_scene",
            set_={
                "index_avg": excluded.index_avg,
                "index_min": excluded.index_min,
                "index_max": excluded.index_max,
                "cloud_pct": excluded.cloud_pct,
                "trust": excluded.trust,
                "updated_at": datetime.now(tz=UTC),
            },
        )
        db.execute(statement)
        return len(rows)

    @staticmethod
    def _publish_new(scenes: list[Scene], existing: set[tuple]) -> int:
        """События scene.ingested по новым сценам (после commit — согласованно)."""
        published = 0
        for scene in scenes:
            key = (scene.field_id, scene.index, scene.captured_at, scene.source_id)
            if key in existing:
                continue
            events.publish(
                SCENE_INGESTED_EVENT,
                {
                    "field_id": scene.field_id,
                    "index": scene.index,
                    "captured_at": scene.captured_at.isoformat(),
                    "index_avg": scene.index_avg,
                    "cloud_pct": scene.cloud_pct,
                    "source_id": scene.source_id,
                    "scene_ref": scene.scene_ref,
                },
            )
            published += 1
        return published

    # -- прогон ---------------------------------------------------------------

    def ingest_window(
        self,
        db: Session,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> IngestReport:
        """Прогон по всем активным полям за окно (по умолчанию lookback_days)."""
        end = date_to or date.today()
        start = date_from or end - timedelta(days=self._lookback_days)
        ok = skipped = 0
        failed: list[str] = []
        published = 0
        for field_id, name in self.active_fields(db):
            try:
                scenes: list[Scene] = []
                for index in self._indices:
                    fetched, error = self._fetch_with_retries(
                        lambda fid=field_id, idx=index: self._adapter.fetch_scenes(
                            fid, idx, start, end, max_cloud_pct=self._max_cloud_pct
                        )
                    )
                    if error is not None:
                        raise SourceUnavailableError(error)
                    scenes.extend(fetched)
            except SourceUnavailableError as exc:
                failed.append(f"{name}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 — одно поле не рушит прогон
                failed.append(f"{name}: {exc}")
                logger.warning("спутник: поле %s — %s", name, exc)
                continue
            if not scenes:
                skipped += 1
                continue
            existing = self._existing_keys(db, scenes)
            ok += self.save_scenes(db, scenes)
            db.commit()
            published += self._publish_new(scenes, existing)
        report = IngestReport(ok=ok, skipped=skipped, failed=failed)
        self.log_source(
            db,
            self._adapter.source_id,
            report.status,
            "; ".join(failed) or None,
        )
        if published:
            logger.info("спутник: опубликовано событий scene.ingested: %d", published)
        return report


__all__ = ["SCENE_INGESTED_EVENT", "IngestReport", "SatelliteIngest", "contour_lookup_factory"]
