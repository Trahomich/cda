"""Тесты API-59 GET /weather/windows, список-режим (docs/06 §7.8 v1.8, #133).

Проверяются: список only_planned=true (default) — только типы из
незавершённых планов внесения текущего сезона; планов нет → items=[] и
hint «нет активных планов операций» (не ошибка); only_planned=false — все
типы из ref_weather_rule; цикл, закрытый уборкой (completed), вегетационные
операции исключает; одиночный режим без изменений + новое поле
nearest_window_date. БД — PostgreSQL (DATABASE_URL_TEST, паттерн #20)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.models.agronomy import CropCycle, FieldOperation
from app.models.auth import UserRole
from app.models.integrations import Forecast
from app.models.references import Culture
from sqlalchemy.orm import Session
from tests.test_auth import auth_header, make_user
from tests.test_inputs import make_cycle, make_culture, make_field, make_ppp
from tests.test_satellite import make_field as make_db_field  # прямая строка в БД

API = "/api/v1"

TODAY = date.today()
#: цикл текущего сезона по году уборки (месяцы 1–8: сначала year, затем −1)
SEASON_YEAR = TODAY.year


def make_plan(
    db: Session,
    cycle: CropCycle,
    *,
    operation_type: str = "spraying",
    status: str = "approved",
    input_id: uuid.UUID | None = None,
):
    """План внесения прямо в БД (модель TreatmentPlan, 03 §2.1)."""
    from app.models.agronomy import TreatmentPlan

    if input_id is None:
        input_id = make_ppp(db).id
    plan = TreatmentPlan(
        cycle_id=cycle.id,
        input_id=input_id,
        dose=1.0,
        operation_type=operation_type,
        planned_date=TODAY + timedelta(days=2),
        status=status,
    )
    db.add(plan)
    db.commit()
    return plan


def make_forecast_days(db: Session, field_id: uuid.UUID) -> None:
    """Горизонт 7 сут: все сутки подходят под любое правило (окно открыто)."""
    for delta in range(7):
        db.add(
            Forecast(
                field_id=field_id,
                forecast_for=TODAY + timedelta(days=delta),
                issued_at=datetime.now(tz=UTC),
                temperature_min_c=12.0,
                temperature_max_c=20.0,
                wind_max_ms=3.0,
                precipitation_mm=0.0,
                humidity_avg_pct=60.0,
            )
        )
    db.commit()


@pytest.fixture
def setup(client, db_session):
    make_user(db_session, login="win-agro", role=UserRole.AGRONOMIST, password="pw-win")
    auth = auth_header(client, "win-agro", "pw-win")
    field_id = make_db_field(db_session, name="Окна-1")
    make_forecast_days(db_session, field_id)
    culture = make_culture(db_session)
    cycle = CropCycle(
        field_id=field_id, year=SEASON_YEAR, culture_id=culture.id, kind="main", status="planned"
    )
    db_session.add(cycle)
    db_session.commit()
    return {"client": client, "auth": auth, "field_id": field_id, "cycle": cycle}


class TestWindowsList:
    def test_list_only_planned_spraying(self, setup):
        """План опрыскивания approved → в списке только spraying (default true)."""
        c, s = setup["client"], setup
        make_plan(s["cycle"] if hasattr(s, "__getitem__") else s["cycle"])
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": s["field_id"]}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["field_id"] == str(s["field_id"])
        assert data["only_planned"] is True
        assert data["hint"] is None
        assert [it["operation_type"] for it in data["items"]] == ["spraying"]
        # структура элемента — WindowResponse с nearest_window_date
        item = data["items"][0]
        assert item["window_open"] is True
        assert item["nearest_window_date"] == TODAY.isoformat()
        assert set(item) == {
            "operation_type",
            "field_id",
            "rule_text",
            "window_open",
            "nearest_window_date",
            "days",
            "alert",
        }

    def test_list_no_plans_hint(self, setup):
        """Планов нет → items=[] и hint, это 200 (не ошибка, §7.8)."""
        c, s = setup["client"], setup
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": s["field_id"]}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["items"] == []
        assert data["hint"] == "нет активных планов операций"

    def test_list_only_planned_false_all_types(self, setup):
        """only_planned=false — все типы из ref_weather_rule."""
        c, s = setup["client"], setup
        make_plan(s["cycle"], operation_type="fertilizing")
        resp = c.get(
            f"{API}/weather/windows",
            headers=s["auth"],
            params={"field_id": s["field_id"], "only_planned": "false"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["only_planned"] is False
        types = [it["operation_type"] for it in data["items"]]
        assert "spraying" in types and "fertilizing" in types
        assert {"spraying", "fertilizing", "seeding", "harvesting"} <= set(types)
        assert data["hint"] is None

    def test_list_multiple_plan_types_distinct(self, setup):
        """Планы spraying + fertilizing → оба типа, без дублей."""
        c, s = setup["client"], setup
        make_plan(s["cycle"], operation_type="spraying")
        make_plan(s["cycle"], operation_type="fertilizing")
        make_plan(s["cycle"], operation_type="spraying", status="completed")
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": s["field_id"]}
        )
        types = [it["operation_type"] for it in resp.json()["items"]]
        assert sorted(types) == ["fertilizing", "spraying"]

    def test_list_finished_plans_excluded(self, setup):
        """Только завершённые/отменённые планы → items=[] + hint."""
        c, s = setup["client"], setup
        make_plan(s["cycle"], status="completed")
        make_plan(s["cycle"], status="cancelled")
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": s["field_id"]}
        )
        assert resp.json()["items"] == []
        assert resp.json()["hint"] == "нет активных планов операций"

    def test_list_harvest_completed_excludes_vegetation(self, setup, db_session):
        """Цикл закрыт уборкой → вегетационные окна не показываются (§7.8)."""
        c, s = setup["client"], setup
        make_plan(s["cycle"], operation_type="spraying")
        s["cycle"].status = "completed"  # урожай убран
        db_session.commit()
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": s["field_id"]}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["hint"] == "нет активных планов операций"
        # only_planned=false работает и после уборки (явный запрос оператора)
        resp = c.get(
            f"{API}/weather/windows",
            headers=s["auth"],
            params={"field_id": s["field_id"], "only_planned": "false"},
        )
        assert len(resp.json()["items"]) >= 4

    def test_list_plans_other_season_excluded(self, setup, db_session):
        """Планы цикла прошлого/будущего сезона не влияют на список."""
        c, s = setup["client"], setup
        old = CropCycle(
            field_id=s["field_id"],
            year=SEASON_YEAR - 2,
            culture_id=s["cycle"].culture_id,
            kind="main",
        )
        db_session.add(old)
        db_session.commit()
        make_plan(old, operation_type="spraying")
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": s["field_id"]}
        )
        assert resp.json()["items"] == []
        assert resp.json()["hint"] == "нет активных планов операций"

    def test_list_field_not_found(self, setup):
        c, s = setup["client"], setup
        resp = c.get(
            f"{API}/weather/windows", headers=s["auth"], params={"field_id": uuid.uuid4()}
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "field_not_found"


class TestWindowSingle:
    def test_single_mode_unchanged_plus_nearest(self, setup):
        """Одиночный режим: прежний контракт + nearest_window_date."""
        c, s = setup["client"], setup
        resp = c.get(
            f"{API}/weather/windows",
            headers=s["auth"],
            params={"field_id": s["field_id"], "operation_type": "spraying"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["operation_type"] == "spraying"
        assert data["window_open"] is True
        assert data["nearest_window_date"] == TODAY.isoformat()
        assert len(data["days"]) == 7

    def test_single_no_window_nearest_null(self, setup, db_session):
        """Окна нет (ветер 40 м/с) → window_open=false, nearest=null, alert."""
        c, s = setup["client"], setup
        for fc in db_session.query(Forecast).filter(Forecast.field_id == s["field_id"]):
            fc.wind_max_ms = 40.0
        db_session.commit()
        resp = c.get(
            f"{API}/weather/windows",
            headers=s["auth"],
            params={"field_id": s["field_id"], "operation_type": "spraying"},
        )
        data = resp.json()
        assert data["window_open"] is False
        assert data["nearest_window_date"] is None
        assert data["alert"] is not None

    def test_single_unknown_operation_type(self, setup):
        c, s = setup["client"], setup
        resp = c.get(
            f"{API}/weather/windows",
            headers=s["auth"],
            params={"field_id": s["field_id"], "operation_type": "flying"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "unknown_operation_type"

    def test_rbac_machine_operator_can_read(self, setup, db_session):
        """Группа weather — все роли (E-06/E-10), машинист-оператор допущен."""
        c, s = setup["client"], setup
        make_user(db_session, login="win-mach", role=UserRole.MACHINE_OPERATOR, password="pw-m2")
        auth = auth_header(c, "win-mach", "pw-m2")
        resp = c.get(
            f"{API}/weather/windows", headers=auth, params={"field_id": s["field_id"]}
        )
        assert resp.status_code == 200
