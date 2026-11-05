"""Тесты модуля техники (задача MVP-7, API-19..21).

Проверяется: CRUD парка с уникальным госномером (409 duplicate_gov_number),
width_required для техник с захватом (422), автомат статусов машины
(retired терминален), задания смены: draft → confirmed/rejected по RULE-TECH-04
(PATCH и API-20 confirm), 409 на недопустимые переходы, связь с операцией (404),
GET /machine-jobs с фильтром status, API-21 maintenance, RBAC (парк —
manager/admin, задания — группа machinery), RFC 7807. PostgreSQL+PostGIS
(DATABASE_URL_TEST, паттерн #20/#22).
"""

import uuid
from datetime import date

from app.models.agronomy import Field, FieldOperation
from app.models.auth import UserRole
from sqlalchemy import select
from tests.test_auth import auth_header, make_user

API = "/api/v1/machines"
JOBS = "/api/v1/machine-jobs"
MAINT = "/api/v1/maintenance"


def boss_headers(client, db_session) -> dict:
    """Руководитель: ведёт парк и ТО (07-ux §5)."""
    make_user(db_session, login="boss_m", role=UserRole.MANAGER)
    return auth_header(client, "boss_m", "secret123")


def oper_headers(client, db_session) -> dict:
    """Механизатор: читает парк, пишет задания (группа machinery)."""
    make_user(db_session, login="oper_m", role=UserRole.MACHINE_OPERATOR)
    return auth_header(client, "oper_m", "secret123")


def create_machine(client, headers, **overrides) -> dict:
    payload = {
        "machine_type": "tractor",
        "model": "John Deere 6120M",
        "gov_number": "А123ВС77",
        "width_m": 3.0,
        "engine_hours": 1500,
    } | overrides
    resp = client.post(API, json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_job(client, headers, machine_id, **overrides) -> dict:
    payload = {
        "machine_id": machine_id,
        "performed_at": str(date.today()),
        "area_ha": 45.5,
        "source": "telemetry",
    } | overrides
    resp = client.post(JOBS, json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestMachinesCrud:
    def test_401_without_token(self, client, db_session):
        assert client.get(API).status_code == 401
        assert client.post(API, json={"machine_type": "truck", "model": "X"}).status_code == 401

    def test_create_201_with_location(self, client, db_session):
        headers = boss_headers(client, db_session)
        body = create_machine(client, headers)
        assert body["machine_type"] == "tractor"
        assert body["status"] == "active"
        assert body["engine_hours"] == 1500.0
        assert client.app is not None
        resp = client.post(
            API,
            json={"machine_type": "combine", "model": "Torum 760"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.headers["location"].startswith(f"{API}/")
        assert resp.json()["gov_number"] is None  # без госномера — допустимо

    def test_duplicate_gov_number_409(self, client, db_session):
        headers = boss_headers(client, db_session)
        create_machine(client, headers, gov_number="Х555ММ50")
        resp = client.post(
            API,
            json={
                "machine_type": "truck",
                "model": "КамАЗ 6520",
                "gov_number": "Х555ММ50",
            },
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "duplicate_gov_number"

    def test_two_machines_without_gov_number_ok(self, client, db_session):
        """Госномер nullable: NULL не конфликтует (partial UQ)."""
        headers = boss_headers(client, db_session)
        create_machine(client, headers, model="Первая без номера")
        resp = client.post(
            API,
            json={"machine_type": "combine", "model": "Вторая без номера"},
            headers=headers,
        )
        assert resp.status_code == 201

    def test_width_required_422(self, client, db_session):
        headers = boss_headers(client, db_session)
        resp = client.post(
            API,
            json={"machine_type": "sprayer", "model": "Hardi Navigator"},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "width_required"
        # truck без ширины захвата — можно
        resp = client.post(
            API, json={"machine_type": "truck", "model": "ГАЗон Next"}, headers=headers
        )
        assert resp.status_code == 201

    def test_unknown_type_400(self, client, db_session):
        resp = client.post(
            API, json={"machine_type": "bulldozer", "model": "X"}, headers=boss_headers(client, db_session)
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "unknown_machine_type"

    def test_list_filters_and_pagination(self, client, db_session):
        headers = boss_headers(client, db_session)
        create_machine(client, headers, model="Т-1")
        create_machine(client, headers, model="Т-2", machine_type="combine", gov_number=None)
        resp = client.get(API, headers=headers)
        assert resp.json()["total"] == 2
        resp = client.get(API, params={"machine_type": "combine"}, headers=headers)
        assert [m["model"] for m in resp.json()["items"]] == ["Т-2"]
        resp = client.get(API, params={"limit": 1}, headers=headers)
        assert len(resp.json()["items"]) == 1

    def test_get_404(self, client, db_session):
        resp = client.get(f"{API}/{uuid.uuid4()}", headers=boss_headers(client, db_session))
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_patch_rename(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers, model="Прежняя")["machine_id"]
        resp = client.patch(f"{API}/{mid}", json={"model": "Новая"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["model"] == "Новая"

    def test_status_automaton(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        resp = client.patch(f"{API}/{mid}", json={"status": "maintenance"}, headers=headers)
        assert resp.status_code == 200
        resp = client.patch(f"{API}/{mid}", json={"status": "active"}, headers=headers)
        assert resp.status_code == 200
        resp = client.patch(f"{API}/{mid}", json={"status": "retired"}, headers=headers)
        assert resp.status_code == 200
        # retired — терминальный: любой переход невозможен
        for target in ("active", "maintenance", "retired"):
            resp = client.patch(f"{API}/{mid}", json={"status": target}, headers=headers)
            if target == "retired":
                assert resp.status_code == 200  # тот же статус — без перехода
            else:
                assert resp.status_code == 422
                assert resp.json()["code"] == "invalid_status_transition"

    def test_delete_retires(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        resp = client.delete(f"{API}/{mid}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "retired"


class TestMachineJobs:
    def test_create_draft_and_list_by_machine(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        body = create_job(client, headers, mid)
        assert body["status"] == "draft"
        assert body["machine_model"] == "John Deere 6120M"
        resp = client.get(f"{API}/{mid}/jobs", headers=headers)
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["job_id"] == body["job_id"]

    def test_create_404_unknown_machine(self, client, db_session):
        headers = boss_headers(client, db_session)
        resp = client.post(
            JOBS,
            json={"machine_id": str(uuid.uuid4()), "performed_at": "2027-05-01", "area_ha": 10},
            headers=headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_create_422_retired_machine(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        client.delete(f"{API}/{mid}", headers=headers)
        resp = client.post(
            JOBS,
            json={"machine_id": mid, "performed_at": "2027-05-01", "area_ha": 10},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "machine_retired"

    def test_field_operation_link(self, client, db_session):
        """Задание связывается с dat_field_operation, если операция существует."""
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        make_user(db_session, login="agro_mj", role=UserRole.AGRONOMIST)
        owner = db_session.scalar(select(Field))
        operation = FieldOperation(
            client_id=uuid.uuid4(),
            field_id=owner.id,
            operation_type="spraying",
            date=date.today(),
            actual_area_ga=10,
            source="manual",
        )
        db_session.add(operation)
        db_session.commit()
        body = create_job(client, headers, mid, field_operation_id=str(operation.id))
        assert body["field_operation_id"] == str(operation.id)
        # несуществующая операция — 404, а не FK-500
        resp = client.post(
            JOBS,
            json={
                "machine_id": mid,
                "performed_at": str(date.today()),
                "area_ha": 5,
                "field_operation_id": str(uuid.uuid4()),
            },
            headers=headers,
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_status_automaton_patch_and_confirm(self, client, db_session):
        """RULE-TECH-04: draft → confirmed/rejected; прочие переходы — 409."""
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        job = create_job(client, headers, mid)
        # API-20 confirm
        resp = client.post(f"{JOBS}/{job['job_id']}/confirm", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "confirmed"
        # повторный confirm — недопустимый переход (confirmed терминален)
        resp = client.post(f"{JOBS}/{job['job_id']}/confirm", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "invalid_job_status_transition"
        # rejected из confirmed — тоже нельзя
        resp = client.patch(f"{JOBS}/{job['job_id']}", json={"status": "rejected"}, headers=headers)
        assert resp.status_code == 409

    def test_patch_reject_with_fact_correction(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        job = create_job(client, headers, mid, area_ha=100)
        resp = client.patch(
            f"{JOBS}/{job['job_id']}",
            json={"status": "confirmed", "area_ha": 98.5, "fuel_l": 210.0},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "confirmed"
        assert body["area_ha"] == 98.5
        assert body["fuel_l"] == 210.0

    def test_patch_unknown_status_400(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        job = create_job(client, headers, mid)
        resp = client.patch(f"{JOBS}/{job['job_id']}", json={"status": "done"}, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == "unknown_job_status"

    def test_list_all_jobs_filter_status(self, client, db_session):
        """API-20: GET /machine-jobs — общий список с фильтром status."""
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        j1 = create_job(client, headers, mid, performed_at="2027-07-01")
        j2 = create_job(client, headers, mid, performed_at="2027-07-02")
        client.post(f"{JOBS}/{j2['job_id']}/confirm", headers=headers)
        resp = client.get(JOBS, headers=headers)
        assert resp.json()["total"] == 2
        resp = client.get(JOBS, params={"status": "confirmed"}, headers=headers)
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["job_id"] == j2["job_id"]
        assert body["items"][0]["job_id"] != j1["job_id"]

    def test_get_jobs_404_unknown_machine(self, client, db_session):
        resp = client.get(f"{API}/{uuid.uuid4()}/jobs", headers=boss_headers(client, db_session))
        assert resp.status_code == 404

    def test_area_negative_400(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        resp = client.post(
            JOBS,
            json={"machine_id": mid, "performed_at": "2027-05-01", "area_ha": -1},
            headers=headers,
        )
        assert resp.status_code == 400


class TestMaintenance:
    def test_create_and_list(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers, engine_hours=1200)["machine_id"]
        resp = client.post(
            MAINT,
            json={
                "machine_id": mid,
                "kind": "service",
                "due_at_hours": 1250,
                "done_at": str(date.today()),
                "done_at_hours": 1230,
                "downtime_h": 4.5,
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers["location"].startswith(f"{MAINT}/")
        body = resp.json()
        assert body["machine_model"] == "John Deere 6120M"
        assert body["downtime_h"] == 4.5
        resp = client.get(MAINT, headers=headers)
        assert resp.json()["total"] == 1
        resp = client.get(MAINT, params={"machine_id": mid}, headers=headers)
        assert resp.json()["total"] == 1
        resp = client.get(MAINT, params={"machine_id": str(uuid.uuid4())}, headers=headers)
        assert resp.json()["total"] == 0

    def test_unknown_kind_400(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers)["machine_id"]
        resp = client.post(
            MAINT, json={"machine_id": mid, "kind": "wash", "due_at_hours": 100}, headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "unknown_maintenance_kind"

    def test_done_hours_above_engine_hours_422(self, client, db_session):
        headers = boss_headers(client, db_session)
        mid = create_machine(client, headers, engine_hours=100)["machine_id"]
        resp = client.post(
            MAINT,
            json={"machine_id": mid, "kind": "inspection", "due_at_hours": 150, "done_at_hours": 140},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "maintenance_hours_exceed_engine_hours"


class TestRbac:
    def test_operator_reads_fleet_but_cannot_write(self, client, db_session):
        headers = oper_headers(client, db_session)
        assert client.get(API, headers=headers).status_code == 200
        resp = client.post(
            API, json={"machine_type": "truck", "model": "X"}, headers=headers
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "insufficient_role"

    def test_operator_creates_and_confirms_job(self, client, db_session):
        """Механизатор пишет задания и подтверждает свои смены (E-18)."""
        boss = boss_headers(client, db_session)
        oper = oper_headers(client, db_session)
        mid = create_machine(client, boss)["machine_id"]
        job = create_job(client, oper, mid, source="manual")
        resp = client.post(f"{JOBS}/{job['job_id']}/confirm", headers=oper)
        assert resp.status_code == 200
        assert resp.json()["status"] == "confirmed"

    def test_operator_cannot_write_maintenance(self, client, db_session):
        boss = boss_headers(client, db_session)
        oper = oper_headers(client, db_session)
        mid = create_machine(client, boss)["machine_id"]
        resp = client.post(
            MAINT, json={"machine_id": mid, "kind": "service", "due_at_hours": 100}, headers=oper
        )
        assert resp.status_code == 403

    def test_livestock_specialist_forbidden(self, client, db_session):
        make_user(db_session, login="zoo_m", role=UserRole.LIVESTOCK_SPECIALIST)
        headers = auth_header(client, "zoo_m", "secret123")
        assert client.get(API, headers=headers).status_code == 403
        assert client.get(JOBS, headers=headers).status_code == 403
        assert client.get(MAINT, headers=headers).status_code == 403

    def test_admin_can_write(self, client, db_session):
        make_user(db_session, login="root_m", role=UserRole.ADMIN)
        headers = auth_header(client, "root_m", "secret123")
        resp = client.post(
            API, json={"machine_type": "loader", "model": "Bobcat S550"}, headers=headers
        )
        assert resp.status_code == 201
