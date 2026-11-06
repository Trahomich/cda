"""Тесты модуля алертов (задача MVP-12, API-27/28, ADR-04).

Проверяется: генераторы (погодный по dat_forecast.hazard на поля с активными
циклами, NDVI-падение >15 % между соседними сценами, спутниковая пауза),
дедупликация активных алертов type+field_id, жизненный цикл статуса
new → read → closed (409 на неверный переход), подписка генераторов на
события шины scene.ingested / forecast.ingested, API-27 (список с фильтрами,
read, PATCH) и API-28 (состояние адаптеров), RBAC групп alerts/sources.
Наружу тесты не ходят; БД — PostgreSQL (DATABASE_URL_TEST, паттерн #20).
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.core import events
from app.models.agronomy import Alert, CropCycle
from app.models.auth import UserRole
from app.models.integrations import Forecast, SatelliteScene, SourceJournalEntry
from app.models.references import Culture, Source
from app.modules.alerts.engine import AlertEngine
from app.modules.alerts.generators import AlertGenerators
from app.modules.alerts.scheduler import create_alerts_scheduler
from app.modules.satellite.ingest import SCENE_INGESTED_EVENT
from app.modules.weather.ingest import FORECAST_INGESTED_EVENT
from sqlalchemy import select
from sqlalchemy import text as db_text
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user

API = "/api/v1"
FIELD_SQUARE = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [
            [
                [40.0, 51.0],
                [40.01, 51.0],
                [40.01, 51.01],
                [40.0, 51.01],
                [40.0, 51.0],
            ]
        ],
    }
)
SATELLITE_SOURCE_ID = "satellite.sentinel2-l2a"
WEATHER_SOURCE_ID = "weather.open-meteo"


def make_field(db: Session, name="Степное-1", status="active") -> uuid.UUID:
    field_id = uuid.uuid4()
    db.execute(
        db_text(
            "INSERT INTO dat_field (id, name, contour, status) "
            "VALUES (:id, :name, ST_GeomFromGeoJSON(:geo)::geography, :status)"
        ),
        {"id": field_id, "name": name, "geo": FIELD_SQUARE, "status": status},
    )
    db.commit()
    return field_id


def make_culture(db: Session) -> Culture:
    culture = Culture(name="Пшеница озимая", kind="main", crop_group="зерновые")
    db.add(culture)
    db.commit()
    return culture


def make_cycle(db: Session, field_id: uuid.UUID, status="in_progress") -> CropCycle:
    culture = make_culture(db)
    cycle = CropCycle(
        field_id=field_id,
        year=date.today().year,
        culture_id=culture.id,
        kind="main",
        status=status,
    )
    db.add(cycle)
    db.commit()
    return cycle


def make_forecast(
    db: Session,
    field_id: uuid.UUID,
    *,
    forecast_for: date,
    hazard: str,
    temperature_min_c: float | None = -3.0,
    precipitation_mm: float | None = 0.0,
) -> Forecast:
    item = Forecast(
        field_id=field_id,
        forecast_for=forecast_for,
        issued_at=datetime.now(tz=UTC),
        temperature_min_c=temperature_min_c,
        temperature_max_c=10.0,
        precipitation_mm=precipitation_mm,
        hazard=hazard,
    )
    db.add(item)
    db.commit()
    return item


def make_scene(
    db: Session,
    field_id: uuid.UUID,
    *,
    captured_at: date,
    index_avg: float,
    index: str = "ndvi",
) -> SatelliteScene:
    db.execute(
        db_text("INSERT INTO ref_source (source_id, kind) VALUES (:s, 'satellite')").bindparams(
            s=SATELLITE_SOURCE_ID
        )
    )
    scene = SatelliteScene(
        field_id=field_id,
        index=index,
        captured_at=captured_at,
        resolution_m=10,
        index_avg=index_avg,
        index_min=index_avg - 0.05,
        index_max=index_avg + 0.05,
        cloud_pct=5.0,
        composite_days=1,
        source_id=SATELLITE_SOURCE_ID,
    )
    db.add(scene)
    db.commit()
    return scene


def alerts(db: Session) -> list[Alert]:
    return list(db.scalars(select(Alert).order_by(Alert.created_at, Alert.id)))


# =============================================================================
# Погодный генератор
# =============================================================================


class TestWeatherGenerator:
    def test_frost_on_field_with_active_cycle(self, db_session):
        """Заморозок в прогнозе на поле с активным циклом → weather_danger critical."""
        field_id = make_field(db_session)
        make_cycle(db_session, field_id)
        make_forecast(
            db_session, field_id, forecast_for=date.today() + timedelta(days=2), hazard="frost"
        )
        report = AlertGenerators().check_weather(db_session)
        assert report.created == 1
        (alert,) = alerts(db_session)
        assert alert.type == "weather_danger"
        assert alert.severity == "critical"
        assert alert.status == "new"
        assert alert.payload["hazard"] == "frost"
        assert alert.payload["data_refs"][0]["entity"] == "forecast"

    def test_heavy_rain_is_warning(self, db_session):
        field_id = make_field(db_session)
        make_cycle(db_session, field_id)
        make_forecast(db_session, field_id, forecast_for=date.today(), hazard="heavy_rain")
        AlertGenerators().check_weather(db_session)
        (alert,) = alerts(db_session)
        assert alert.severity == "warning"

    def test_worst_hazard_wins(self, db_session):
        """Несколько дней с явлениями: критичнее всех — frost (critical)."""
        field_id = make_field(db_session)
        make_cycle(db_session, field_id)
        make_forecast(db_session, field_id, forecast_for=date.today(), hazard="wind")
        make_forecast(
            db_session, field_id, forecast_for=date.today() + timedelta(days=1), hazard="frost"
        )
        AlertGenerators().check_weather(db_session)
        (alert,) = alerts(db_session)
        assert alert.severity == "critical"
        assert alert.payload["hazard"] == "frost"

    def test_no_alert_without_active_cycle(self, db_session):
        """Опасный прогноз на поле без цикла (или завершённом) — алерта нет."""
        field_id = make_field(db_session)
        make_cycle(db_session, field_id, status="completed")
        make_forecast(db_session, field_id, forecast_for=date.today(), hazard="frost")
        report = AlertGenerators().check_weather(db_session)
        assert report.created == 0
        assert alerts(db_session) == []

    def test_old_forecast_out_of_horizon_ignored(self, db_session):
        field_id = make_field(db_session)
        make_cycle(db_session, field_id)
        make_forecast(
            db_session, field_id, forecast_for=date.today() + timedelta(days=30), hazard="frost"
        )
        assert AlertGenerators().check_weather(db_session).created == 0

    def test_duplication_skipped_until_closed(self, db_session):
        """Повторный прогон не дублирует активный алерт; после закрытия — создаёт."""
        field_id = make_field(db_session)
        make_cycle(db_session, field_id)
        make_forecast(db_session, field_id, forecast_for=date.today(), hazard="frost")
        gen = AlertGenerators()
        assert gen.check_weather(db_session).created == 1
        report = gen.check_weather(db_session)
        assert report.created == 0
        assert report.duplicates == 1
        assert len(alerts(db_session)) == 1
        # закрыли — дедупликация больше не мешает
        db_session.execute(
            db_text("UPDATE dat_alert SET status = 'closed' WHERE field_id = :f"),
            {"f": str(field_id)},
        )
        db_session.commit()
        assert gen.check_weather(db_session).created == 1
        assert len(alerts(db_session)) == 2


# =============================================================================
# NDVI-генератор
# =============================================================================


class TestNdviGenerator:
    def test_drop_over_threshold(self, db_session):
        """mean NDVI упал более чем на 15 % между соседними сценами за ≤10 дн."""
        field_id = make_field(db_session)
        make_scene(
            db_session, field_id, captured_at=date.today() - timedelta(days=6), index_avg=0.60
        )
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.48)
        report = AlertGenerators().check_ndvi(db_session)
        assert report.created == 1
        (alert,) = alerts(db_session)
        assert alert.type == "ndvi_anomaly"
        assert alert.severity == "warning"
        assert alert.payload["drop_pct"] == pytest.approx(20.0, abs=0.1)
        assert len(alert.payload["data_refs"]) == 2

    def test_small_drop_no_alert(self, db_session):
        field_id = make_field(db_session)
        make_scene(
            db_session, field_id, captured_at=date.today() - timedelta(days=6), index_avg=0.60
        )
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.56)
        assert AlertGenerators().check_ndvi(db_session).created == 0

    def test_window_too_wide_no_alert(self, db_session):
        """Сцены через 11 дней не соседние — сравнивать нельзя."""
        field_id = make_field(db_session)
        make_scene(
            db_session, field_id, captured_at=date.today() - timedelta(days=11), index_avg=0.60
        )
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.30)
        assert AlertGenerators().check_ndvi(db_session).created == 0

    def test_single_scene_no_alert(self, db_session):
        field_id = make_field(db_session)
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.4)
        assert AlertGenerators().check_ndvi(db_session).created == 0

    def test_growth_no_alert(self, db_session):
        field_id = make_field(db_session)
        make_scene(
            db_session, field_id, captured_at=date.today() - timedelta(days=6), index_avg=0.30
        )
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.55)
        assert AlertGenerators().check_ndvi(db_session).created == 0


# =============================================================================
# Спутниковая пауза
# =============================================================================


class TestSatelliteGap:
    def test_stale_scene_alerts(self, db_session):
        """Последняя сцена старше 14 сут → data_source info."""
        field_id = make_field(db_session)
        make_scene(
            db_session, field_id, captured_at=date.today() - timedelta(days=20), index_avg=0.5
        )
        report = AlertGenerators().check_satellite_gap(db_session)
        assert report.created == 1
        (alert,) = alerts(db_session)
        assert alert.type == "data_source"
        assert alert.severity == "info"

    def test_no_scenes_alerts_too(self, db_session):
        make_field(db_session)
        assert AlertGenerators().check_satellite_gap(db_session).created == 1

    def test_fresh_scene_no_alert(self, db_session):
        field_id = make_field(db_session)
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.5)
        assert AlertGenerators().check_satellite_gap(db_session).created == 0


# =============================================================================
# Движок: события шины
# =============================================================================


class TestAlertEngine:
    def test_scene_ingested_event_generates(self, db_session, db_engine):
        """scene.ingested → прогон NDVI-проверки и паузы (своя сессия БД)."""
        from sqlalchemy.orm import sessionmaker

        field_id = make_field(db_session)
        make_scene(
            db_session, field_id, captured_at=date.today() - timedelta(days=6), index_avg=0.60
        )
        make_scene(db_session, field_id, captured_at=date.today(), index_avg=0.40)
        engine = AlertEngine(session_factory=sessionmaker(bind=db_engine, expire_on_commit=False))
        engine.subscribe()
        try:
            events.publish(
                SCENE_INGESTED_EVENT,
                {"field_id": str(field_id), "index": "ndvi", "index_avg": 0.4},
            )
        finally:
            engine.unsubscribe()
        types = {a.type for a in alerts(db_session)}
        assert "ndvi_anomaly" in types

    def test_forecast_ingested_event_generates(self, db_session, db_engine):
        from sqlalchemy.orm import sessionmaker

        field_id = make_field(db_session)
        make_cycle(db_session, field_id)
        make_forecast(
            db_session, field_id, forecast_for=date.today() + timedelta(days=1), hazard="hail"
        )
        engine = AlertEngine(session_factory=sessionmaker(bind=db_engine, expire_on_commit=False))
        engine.subscribe()
        try:
            events.publish(FORECAST_INGESTED_EVENT, {"fields": 1})
        finally:
            engine.unsubscribe()
        assert alerts(db_session)[0].type == "weather_danger"

    def test_scheduler_disabled_by_default(self):
        from app.core.config import Settings

        assert create_alerts_scheduler(Settings()) is None


# =============================================================================
# API-27: список, read, PATCH, жизненный цикл
# =============================================================================


class TestApi27:
    @pytest.fixture()
    def agro(self, db_session):
        make_user(db_session, login="agro", role=UserRole.AGRONOMIST)
        return "agro"

    @staticmethod
    def auth(client, login):
        return auth_header(client, login, "secret123")

    @staticmethod
    def make_alert(
        db: Session,
        *,
        type="ndvi_anomaly",
        status="new",  # noqa: A002
        field_id=None,
    ) -> Alert:
        alert = Alert(
            type=type,
            severity="warning",
            field_id=field_id,
            payload={"message": "тест", "data_refs": []},
            status=status,
        )
        db.add(alert)
        db.commit()
        return alert

    def test_list_with_filters_and_pagination(self, client, db_session, agro):
        h = self.auth(client, agro)
        f1, f2 = make_field(db_session, "А"), make_field(db_session, "Б")
        self.make_alert(db_session, type="weather_danger", field_id=f1)
        self.make_alert(db_session, type="ndvi_anomaly", field_id=f2)
        self.make_alert(db_session, type="ndvi_anomaly", field_id=f1)

        resp = client.get(f"{API}/alerts", headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 3
        assert body["limit"] == 50 and body["offset"] == 0
        assert {i["type"] for i in body["items"]} == {"weather_danger", "ndvi_anomaly"}

        resp = client.get(f"{API}/alerts?type=ndvi_anomaly", headers=h)
        assert resp.json()["total"] == 2

        resp = client.get(f"{API}/alerts?field_id={f2}", headers=h)
        assert resp.json()["total"] == 1

        resp = client.get(f"{API}/alerts?limit=1&offset=1", headers=h)
        assert len(resp.json()["items"]) == 1

    def test_unknown_filter_422(self, client, db_session, agro):
        h = self.auth(client, agro)
        for bad in (f"{API}/alerts?type=nonsense", f"{API}/alerts?status=deleted"):
            resp = client.get(bad, headers=h)
            assert resp.status_code == 422
            assert resp.headers["content-type"].startswith("application/problem+json")

    def test_mark_read_and_lifecycle(self, client, db_session, agro):
        h = self.auth(client, agro)
        alert = self.make_alert(db_session)
        # new → read
        resp = client.post(f"{API}/alerts/{alert.id}/read", headers=h)
        assert resp.status_code == 200 and resp.json()["status"] == "read"
        # повторный read идемпотентен
        resp = client.post(f"{API}/alerts/{alert.id}/read", headers=h)
        assert resp.status_code == 200
        # read → closed (PATCH)
        resp = client.patch(f"{API}/alerts/{alert.id}", headers=h, json={"status": "closed"})
        assert resp.status_code == 200 and resp.json()["status"] == "closed"
        # closed → read недопустим: 409
        resp = client.patch(f"{API}/alerts/{alert.id}", headers=h, json={"status": "read"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "alert_invalid_transition"

    def test_patch_new_to_closed_allowed(self, client, db_session, agro):
        h = self.auth(client, agro)
        alert = self.make_alert(db_session)
        resp = client.patch(f"{API}/alerts/{alert.id}", headers=h, json={"status": "closed"})
        assert resp.status_code == 200 and resp.json()["status"] == "closed"

    def test_patch_unknown_status_422(self, client, db_session, agro):
        h = self.auth(client, agro)
        alert = self.make_alert(db_session)
        resp = client.patch(f"{API}/alerts/{alert.id}", headers=h, json={"status": "resolved"})
        assert resp.status_code == 422

    def test_unknown_alert_404(self, client, db_session, agro):
        h = self.auth(client, agro)
        resp = client.post(f"{API}/alerts/{uuid.uuid4()}/read", headers=h)
        assert resp.status_code == 404

    def test_rbac_all_roles_allowed(self, client, db_session):
        """Группа alerts (E-13) — все роли; аноним — 401."""
        make_user(db_session, login="mech", role=UserRole.MACHINE_OPERATOR)
        resp = client.get(f"{API}/alerts", headers=self.auth(client, "mech"))
        assert resp.status_code == 200
        assert client.get(f"{API}/alerts").status_code == 401


# =============================================================================
# API-28: состояние адаптеров
# =============================================================================


class TestApi28:
    def test_sources_status(self, client, db_session):
        make_user(db_session, login="mgr", role=UserRole.MANAGER)
        h = auth_header(client, "mgr", "secret123")
        db_session.add(Source(source_id=WEATHER_SOURCE_ID, kind="weather"))
        db_session.add(Source(source_id=SATELLITE_SOURCE_ID, kind="satellite"))
        db_session.commit()
        db_session.add(
            SourceJournalEntry(
                source_id=WEATHER_SOURCE_ID,
                at=datetime.now(tz=UTC),
                status="degraded",
                reason="таймаут",
            )
        )
        db_session.commit()
        db_session.execute(
            db_text(
                "UPDATE ref_source SET last_status = 'degraded', "
                "last_success_at = now() WHERE source_id = :s"
            ),
            {"s": WEATHER_SOURCE_ID},
        )
        db_session.commit()

        resp = client.get(f"{API}/sources/status", headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        by_id = {i["source_id"]: i for i in body["items"]}
        weather = by_id[WEATHER_SOURCE_ID]
        assert weather["kind"] == "weather"
        assert weather["enabled"] is True
        assert weather["last_status"] == "degraded"
        assert weather["last_success_at"] is not None
        assert weather["last_event"]["status"] == "degraded"
        assert weather["last_event"]["reason"] == "таймаут"
        assert by_id[SATELLITE_SOURCE_ID]["last_event"] is None

    def test_rbac_manager_only(self, client, db_session):
        """Группа sources (E-14) — руководитель и админ; механизатор — 403."""
        make_user(db_session, login="mech2", role=UserRole.MACHINE_OPERATOR)
        h = auth_header(client, "mech2", "secret123")
        assert client.get(f"{API}/sources/status", headers=h).status_code == 403
