"""Тесты журнала операций (задача MVP-9, API-17/18).

Проверяется: запись факта с планом и без (RULE-AGRO-11: отклонение дозы
>10 % → deviation_flag + rationale, 422 не возвращаем), ровно 10 % — не
отклонение; идемпотентность по client_id (повтор — 200 без дубля);
урожайность в операции уборки (UQ cycle+date+source → 409); журнал с
фильтрами field_id/date_from/date_to и пагинацией; 400/404/401/403.

Тестовый контур — PostgreSQL+PostGIS (паттерн #20).
"""

import uuid
from datetime import date

import pytest
from app.models.agronomy import (
    AgroInput,
    CropCycle,
    Field,
    RotationPlan,
    TreatmentPlan,
)
from app.models.auth import UserRole
from app.models.references import Culture
from geoalchemy2 import WKTElement
from tests.test_auth import auth_header, make_user

API = "/api/v1/operations"
POLYGON_WKT = "POLYGON((40.0 0.0, 40.01 0.0, 40.01 0.01, 40.0 0.01, 40.0 0.0))"
CID = uuid.uuid4  # client_id-генератор для читаемости


@pytest.fixture
def agro_headers(client, db_session) -> dict:
    make_user(db_session, login="agro_ops", role=UserRole.AGRONOMIST)
    return auth_header(client, "agro_ops", "secret123")


@pytest.fixture
def demo(db_session):
    """Поле, культура, цикл, препарат и согласованный план дозы 2.0."""
    field = Field(name="Журнальное", contour=WKTElement(POLYGON_WKT, srid=4326))
    db_session.add(field)
    db_session.flush()
    plan_row = RotationPlan(field_id=field.id, year_from=2027, year_to=2031)
    db_session.add(plan_row)
    culture = Culture(name="Пшеница", kind="main", forage_flag=False, crop_group="зерновые")
    db_session.add(culture)
    db_session.flush()
    cycle = CropCycle(field_id=field.id, year=2027, culture_id=culture.id, kind="main")
    db_session.add(cycle)
    input_ = AgroInput(kind="ppp", name="Препарат Y", unit="l_ha", waiting_days=20)
    db_session.add(input_)
    db_session.flush()
    treatment = TreatmentPlan(
        cycle_id=cycle.id,
        input_id=input_.id,
        dose=2.0,
        operation_type="spraying",
        planned_date=date(2027, 6, 10),
        status="approved",
    )
    db_session.add(treatment)
    db_session.commit()
    return {"field": field, "cycle": cycle, "input": input_, "plan": treatment}


def op_body(demo, **kw) -> dict:
    base = {
        "client_id": str(CID()),
        "field_id": str(demo["field"].id),
        "cycle_id": str(demo["cycle"].id),
        "operation_type": "spraying",
        "date": "2027-06-10",
        "input_id": str(demo["input"].id),
        "actual_dose": 2.0,
        "actual_area_ga": 100.0,
        "performer": "Петров И.",
        "source": "manual",
    }
    base.update(kw)
    return base


class TestCreate:
    def test_create_with_plan_no_deviation(self, client, db_session, agro_headers, demo):
        resp = client.post(API, json=op_body(demo), headers=agro_headers)
        assert resp.status_code == 201, resp.text
        assert resp.headers["location"].endswith(resp.json()["operation_id"])
        body = resp.json()
        assert body["deviation_flag"] is False
        assert body["deviation_rationale"] is None
        assert body["status"] == "done"

    def test_deviation_over_10_percent(self, client, db_session, agro_headers, demo):
        resp = client.post(API, json=op_body(demo, actual_dose=2.5), headers=agro_headers)
        assert resp.status_code == 201  # 422 не возвращаем (факт важнее плана)
        body = resp.json()
        assert body["deviation_flag"] is True
        assert "RULE-AGRO-11" in body["deviation_rationale"]
        assert "+25 %" in body["deviation_rationale"]

    def test_deviation_exactly_10_percent_is_not_flagged(
        self, client, db_session, agro_headers, demo
    ):
        resp = client.post(API, json=op_body(demo, actual_dose=2.2), headers=agro_headers)
        body = resp.json()
        assert body["deviation_flag"] is False

    def test_create_without_plan(self, client, db_session, agro_headers, demo):
        # препарата нет в плане: иного input не существует — операция вне плана
        other = AgroInput(kind="ppp", name="Препарат Z", unit="l_ha", waiting_days=10)
        db_session.add(other)
        db_session.commit()
        resp = client.post(API, json=op_body(demo, input_id=str(other.id)), headers=agro_headers)
        assert resp.status_code == 201
        assert resp.json()["deviation_flag"] is False

    def test_no_cycle_no_deviation(self, client, db_session, agro_headers, demo):
        resp = client.post(API, json=op_body(demo, cycle_id=None), headers=agro_headers)
        assert resp.status_code == 201
        assert resp.json()["deviation_flag"] is False

    def test_unknown_field_404(self, client, db_session, agro_headers, demo):
        resp = client.post(
            API, json=op_body(demo, field_id=str(uuid.uuid4())), headers=agro_headers
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_unknown_type_and_source_400(self, client, db_session, agro_headers, demo):
        resp = client.post(API, json=op_body(demo, operation_type="watering"), headers=agro_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == "unknown_operation_type"
        resp = client.post(API, json=op_body(demo, source="sms"), headers=agro_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == "unknown_operation_source"


class TestIdempotency:
    def test_same_client_id_returns_existing_200(self, client, db_session, agro_headers, demo):
        payload = op_body(demo)
        first = client.post(API, json=payload, headers=agro_headers)
        assert first.status_code == 201
        second = client.post(API, json=payload, headers=agro_headers)
        assert second.status_code == 200
        assert second.json()["operation_id"] == first.json()["operation_id"]
        # дубля нет: в журнале одна операция
        journal = client.get(API, params={"field_id": str(demo["field"].id)}, headers=agro_headers)
        assert journal.json()["total"] == 1

    def test_without_client_id_creates_two(self, client, db_session, agro_headers, demo):
        payload = op_body(demo, client_id=None)
        assert client.post(API, json=payload, headers=agro_headers).status_code == 201
        assert client.post(API, json=payload, headers=agro_headers).status_code == 201
        journal = client.get(API, params={"field_id": str(demo["field"].id)}, headers=agro_headers)
        assert journal.json()["total"] == 2


class TestYield:
    def test_harvesting_creates_yield_record(self, client, db_session, agro_headers, demo):
        resp = client.post(
            API,
            json=op_body(
                demo,
                operation_type="harvesting",
                input_id=None,
                actual_dose=None,
                yield_record={"yield_c_ha": 44.5, "gross_t": 210.4, "source": "scales"},
            ),
            headers=agro_headers,
        )
        assert resp.status_code == 201, resp.text
        record = resp.json()["yield_record"]
        assert record["yield_c_ha"] == 44.5
        assert record["source"] == "scales"
        assert record["date"] == "2027-06-10"

    def test_yield_duplicate_409(self, client, db_session, agro_headers, demo):
        body = op_body(
            demo,
            operation_type="harvesting",
            input_id=None,
            actual_dose=None,
            yield_record={"yield_c_ha": 44.5, "source": "scales"},
        )
        assert client.post(API, json=body, headers=agro_headers).status_code == 201
        body2 = dict(body, client_id=str(CID()))
        resp = client.post(API, json=body2, headers=agro_headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "duplicate_yield_record"

    def test_yield_requires_harvesting_and_cycle(self, client, db_session, agro_headers, demo):
        resp = client.post(
            API,
            json=op_body(demo, yield_record={"yield_c_ha": 40.0}),
            headers=agro_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "yield_requires_harvesting"
        resp = client.post(
            API,
            json=op_body(
                demo,
                operation_type="harvesting",
                input_id=None,
                actual_dose=None,
                cycle_id=None,
                yield_record={"yield_c_ha": 40.0},
            ),
            headers=agro_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "yield_requires_cycle"


class TestJournal:
    @pytest.fixture
    def filled(self, client, db_session, agro_headers, demo):
        rows = [
            ("2027-05-01", "tillage"),
            ("2027-06-10", "spraying"),
            ("2027-07-20", "harvesting"),
        ]
        ids = []
        for day, kind in rows:
            body = op_body(
                demo,
                date=day,
                operation_type=kind,
                input_id=None if kind != "spraying" else str(demo["input"].id),
                actual_dose=2.0 if kind == "spraying" else None,
            )
            resp = client.post(API, json=body, headers=agro_headers)
            assert resp.status_code == 201, resp.text
            ids.append(resp.json()["operation_id"])
        return ids

    def test_filters_date_range(self, client, db_session, agro_headers, demo, filled):
        resp = client.get(
            API,
            params={"date_from": "2027-06-01", "date_to": "2027-06-30"},
            headers=agro_headers,
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["operation_type"] == "spraying"

    def test_filter_field_id(self, client, db_session, agro_headers, demo, filled):
        resp = client.get(API, params={"field_id": str(demo["field"].id)}, headers=agro_headers)
        assert resp.json()["total"] == 3
        resp = client.get(API, params={"field_id": str(uuid.uuid4())}, headers=agro_headers)
        assert resp.json()["total"] == 0

    def test_pagination_and_desc_order(self, client, db_session, agro_headers, demo, filled):
        page = client.get(API, params={"limit": 2, "offset": 0}, headers=agro_headers).json()
        assert page["total"] == 3 and len(page["items"]) == 2
        assert page["items"][0]["date"] == "2027-07-20"  # новые сверху
        rest = client.get(API, params={"limit": 2, "offset": 2}, headers=agro_headers).json()
        assert len(rest["items"]) == 1

    def test_get_by_id(self, client, db_session, agro_headers, demo, filled):
        resp = client.get(f"{API}/{filled[1]}", headers=agro_headers)
        assert resp.status_code == 200
        assert resp.json()["operation_id"] == filled[1]
        assert client.get(f"{API}/{uuid.uuid4()}", headers=agro_headers).status_code == 404


class TestAccess:
    def test_machine_operator_can_write(self, client, db_session, demo):
        make_user(db_session, login="oper_ops", role=UserRole.MACHINE_OPERATOR)
        headers = auth_header(client, "oper_ops", "secret123")
        assert client.post(API, json=op_body(demo), headers=headers).status_code == 201

    def test_livestock_403(self, client, db_session, demo):
        make_user(db_session, login="zoo_ops", role=UserRole.LIVESTOCK_SPECIALIST)
        headers = auth_header(client, "zoo_ops", "secret123")
        resp = client.post(API, json=op_body(demo), headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "insufficient_role"

    def test_no_token_401(self, client, db_session):
        assert client.post(API, json={"field_id": str(uuid.uuid4())}).status_code == 401
        assert client.get(API).status_code == 401
