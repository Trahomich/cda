"""Тесты единой операции §7.10 (#173): план → правка → закрытие фактом →
подтверждение, техника 1:N, расходники, стоимости, фильтры API-18.
Полиморфный POST: факт (date) как раньше, план (planned_date) — новый.
"""

import uuid

import pytest
from app.models.agronomy import AgroInput, CropCycle, Field, RotationPlan
from app.models.auth import UserRole
from app.models.machinery import Machine
from app.models.references import Culture
from geoalchemy2 import WKTElement
from tests.test_auth import auth_header, make_user

API = "/api/v1/operations"
POLYGON_WKT = "POLYGON((40.0 0.0, 40.01 0.0, 40.01 0.01, 40.0 0.01, 40.0 0.0))"


@pytest.fixture
def ops_headers(client, db_session) -> dict:
    make_user(db_session, login="ops_unified", role=UserRole.AGRONOMIST)
    return auth_header(client, "ops_unified", "secret123")


@pytest.fixture
def world(db_session):
    """Поле + цикл 2026 + препарат + трактор и прицеп."""
    field = Field(name="Единое", contour=WKTElement(POLYGON_WKT, srid=4326))
    db_session.add(field)
    db_session.flush()
    db_session.add(RotationPlan(field_id=field.id, year_from=2026, year_to=2030))
    culture = Culture(name="Пшеница", kind="main", forage_flag=False, crop_group="зерновые")
    db_session.add(culture)
    db_session.flush()
    cycle = CropCycle(field_id=field.id, year=2026, culture_id=culture.id, kind="main")
    db_session.add(cycle)
    input_ = AgroInput(kind="ppp", name="Препарат Z", unit="l_ha", waiting_days=15)
    db_session.add(input_)
    tractor = Machine(model="К-744", machine_type="tractor", gov_number="А123ВС")
    sprayer = Machine(model="ОП-2000", machine_type="sprayer", gov_number="В456ЕК")
    db_session.add_all([tractor, sprayer])
    db_session.commit()
    return {
        "field": field,
        "cycle": cycle,
        "input": input_,
        "tractor": tractor,
        "sprayer": sprayer,
    }


class TestPlanFactConfirmFlow:
    def test_full_flow(self, client, db_session, ops_headers, world):
        """План → PATCH → complete (расход/техника) → confirm (идемпотентно)."""
        plan_body = {
            "field_id": str(world["field"].id),
            "operation_type": "spraying",
            "planned_date": "2026-06-10",
            "input_id": str(world["input"].id),
            "input_dose": 2.0,
            "cost_plan_mode": "unit",
            "cost_plan_value": 350.0,
        }
        resp = client.post(API, json=plan_body, headers=ops_headers)
        assert resp.status_code == 201, resp.text
        op = resp.json()
        oid = op["operation_id"]
        assert op["status"] == "planned"
        assert op["planned_date"] == "2026-06-10" and op["fact_date"] is None
        assert op["date"] == "2026-06-10"  # совместимость: fact ?? planned
        assert op["input_name"] == "Препарат Z"
        assert op["cycle_id"] == str(world["cycle"].id)  # сезон найден сам
        assert op["cost_plan_rub"] == pytest.approx(350.0 * op["actual_area_ga"])
        assert op["machines"] == [] and op["confirmed_at"] is None

        # правка плана
        resp = client.patch(
            f"{API}/{oid}", json={"planned_date": "2026-06-12"}, headers=ops_headers
        )
        assert resp.status_code == 200
        assert resp.json()["planned_date"] == "2026-06-12"
        assert resp.json()["date"] == "2026-06-12"

        # закрытие фактом: расход напрямую + техника (трактор + прицеп)
        complete_body = {
            "fact_date": "2026-06-13",
            "input_amount_total": 190.0,
            "performer": "Иванов А.",
            "machines": [
                {
                    "machine_id": str(world["tractor"].id),
                    "trailer_id": str(world["sprayer"].id),
                    "operator": "Петров П.",
                    "hours": 6.5,
                }
            ],
        }
        resp = client.post(f"{API}/{oid}/complete", json=complete_body, headers=ops_headers)
        assert resp.status_code == 200, resp.text
        done = resp.json()
        assert done["status"] == "done" and done["fact_date"] == "2026-06-13"
        assert done["input_amount_total"] == 190.0
        assert done["performer"] == "Иванов А."
        assert len(done["machines"]) == 1
        machine = done["machines"][0]
        assert machine["machine_name"].startswith("К-744")
        assert machine["trailer_name"].startswith("ОП-2000")
        assert machine["hours"] == 6.5
        assert done["confirmed_at"] is None

        # подтверждение: факт закрывается пользователем, идемпотентно
        resp = client.post(f"{API}/{oid}/confirm", headers=ops_headers)
        assert resp.status_code == 200
        confirmed = resp.json()
        assert confirmed["confirmed_at"] is not None and confirmed["confirmed_by"] is not None
        again = client.post(f"{API}/{oid}/confirm", headers=ops_headers)
        assert again.status_code == 200
        assert again.json()["confirmed_at"] == confirmed["confirmed_at"]

        # после факта: правка и повторное закрытие — 422
        assert (
            client.patch(
                f"{API}/{oid}", json={"planned_date": "2026-07-01"}, headers=ops_headers
            ).status_code
            == 422
        )
        r = client.post(
            f"{API}/{oid}/complete", json={"fact_date": "2026-06-14"}, headers=ops_headers
        )
        assert r.status_code == 422 and r.json()["code"] == "operation_already_done"

    def test_amount_from_dose_x_area(self, client, db_session, ops_headers, world):
        """complete без расхода всего: доза × площадь поля на дату факта."""
        resp = client.post(
            API,
            json={
                "field_id": str(world["field"].id),
                "operation_type": "fertilizing",
                "planned_date": "2026-05-01",
                "input_id": str(world["input"].id),
                "input_dose": 2.0,
            },
            headers=ops_headers,
        )
        assert resp.status_code == 201, resp.text
        oid = resp.json()["operation_id"]
        resp = client.post(
            f"{API}/{oid}/complete", json={"fact_date": "2026-05-02"}, headers=ops_headers
        )
        assert resp.status_code == 200
        done = resp.json()
        assert done["input_amount_total"] == pytest.approx(2.0 * done["actual_area_ga"], rel=0.01)


class TestPolymorphicPost:
    def test_fact_body_still_works(self, client, db_session, ops_headers, world):
        """Тело факта (date + client_id) — прежнее поведение API-17."""
        body = {
            "client_id": str(uuid.uuid4()),
            "field_id": str(world["field"].id),
            "operation_type": "tillage",
            "date": "2026-06-01",
            "actual_area_ga": 100.0,
            "source": "manual",
        }
        resp = client.post(API, json=body, headers=ops_headers)
        assert resp.status_code == 201, resp.text
        op = resp.json()
        assert op["status"] == "done" and op["fact_date"] == "2026-06-01"
        assert op["planned_date"] is None

    def test_plan_requires_planned_date(self, client, db_session, ops_headers, world):
        """Тело плана без planned_date — 422 валидации pydantic."""
        body = {"field_id": str(world["field"].id), "operation_type": "tillage"}
        resp = client.post(API, json=body, headers=ops_headers)
        assert resp.status_code == 422


class TestFilters:
    def test_mode_year_confirmed_filters(self, client, db_session, ops_headers, world):
        """mode=plan|fact, year сезона, confirmed — срезы единого журнала."""
        # план (не закрыт)
        p1 = client.post(
            API,
            json={
                "field_id": str(world["field"].id),
                "operation_type": "spraying",
                "planned_date": "2026-06-10",
            },
            headers=ops_headers,
        ).json()
        # план → факт подтверждён
        p2 = client.post(
            API,
            json={
                "field_id": str(world["field"].id),
                "operation_type": "harvesting",
                "planned_date": "2026-08-01",
            },
            headers=ops_headers,
        ).json()
        client.post(
            f"{API}/{p2['operation_id']}/complete",
            json={"fact_date": "2026-08-02"},
            headers=ops_headers,
        )
        client.post(f"{API}/{p2['operation_id']}/confirm", headers=ops_headers)

        fid = str(world["field"].id)
        plans = client.get(
            API, params={"field_id": fid, "mode": "plan"}, headers=ops_headers
        ).json()
        assert {i["operation_id"] for i in plans["items"]} == {p1["operation_id"]}

        facts = client.get(
            API, params={"field_id": fid, "mode": "fact"}, headers=ops_headers
        ).json()
        assert {i["operation_id"] for i in facts["items"]} == {p2["operation_id"]}

        year26 = client.get(API, params={"field_id": fid, "year": 2026}, headers=ops_headers).json()
        assert year26["total"] >= 2  # обе операции в сезоне цикла 2026
        year27 = client.get(API, params={"field_id": fid, "year": 2027}, headers=ops_headers).json()
        assert year27["total"] == 0

        confirmed = client.get(
            API, params={"field_id": fid, "confirmed": "true"}, headers=ops_headers
        ).json()
        assert {i["operation_id"] for i in confirmed["items"]} == {p2["operation_id"]}

    def test_invalid_mode_422(self, client, ops_headers):
        resp = client.get(API, params={"mode": "nope"}, headers=ops_headers)
        assert resp.status_code == 422


class TestCompleteErrors:
    def test_confirm_plan_422(self, client, db_session, ops_headers, world):
        """confirm по плану (fact_date пуст) — 422 operation_not_done."""
        p = client.post(
            API,
            json={
                "field_id": str(world["field"].id),
                "operation_type": "tillage",
                "planned_date": "2026-06-10",
            },
            headers=ops_headers,
        ).json()
        resp = client.post(f"{API}/{p['operation_id']}/confirm", headers=ops_headers)
        assert resp.status_code == 422 and resp.json()["code"] == "operation_not_done"

    def test_complete_unknown_machine_404(self, client, db_session, ops_headers, world):
        p = client.post(
            API,
            json={
                "field_id": str(world["field"].id),
                "operation_type": "tillage",
                "planned_date": "2026-06-10",
            },
            headers=ops_headers,
        ).json()
        resp = client.post(
            f"{API}/{p['operation_id']}/complete",
            json={"fact_date": "2026-06-11", "machines": [{"machine_id": str(uuid.uuid4())}]},
            headers=ops_headers,
        )
        assert resp.status_code == 404
