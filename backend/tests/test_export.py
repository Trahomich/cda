"""Тесты экспорта данных хозяйства (задача MVP-14, API-32, REQ-NF-007).

Проверяется: оба формата по каждой entity белого списка, CSV с BOM и «;»,
GeoJSON-геометрия, пустой результат, 400 на неизвестную entity/format, роли
(export — manager/admin). Тестовый контур — PostgreSQL+PostGIS (паттерн #20).
"""

from datetime import date

import pytest
from app.models.agronomy import (
    AgroInput,
    Alert,
    CropCycle,
    Field,
    FieldOperation,
    RotationPlan,
    TreatmentPlan,
)
from app.models.auth import UserRole
from app.models.machinery import Machine, MachineJob
from app.models.references import Culture
from geoalchemy2 import WKTElement
from tests.test_auth import auth_header, make_user

API = "/api/v1/export"

ALL_ENTITIES = [
    "fields",
    "rotation_plans",
    "crop_cycles",
    "agro_inputs",
    "treatment_plans",
    "operations",
    "machines",
    "machine_jobs",
    "alerts",
]

POLYGON_WKT = "POLYGON((40.0 0.0, 40.01 0.0, 40.01 0.01, 40.0 0.01, 40.0 0.0))"


@pytest.fixture
def manager_headers(client, db_session) -> dict:
    make_user(db_session, login="boss_ex", role=UserRole.MANAGER)
    return auth_header(client, "boss_ex", "secret123")


@pytest.fixture
def demo_data(db_session):
    """По одной записи в каждой entity белого списка (с FK-цепочкой)."""
    field = Field(
        name="Экспортное",
        contour=WKTElement(POLYGON_WKT, srid=4326),
    )
    db_session.add(field)
    db_session.flush()
    db_session.add(RotationPlan(field_id=field.id, year_from=2026, year_to=2030))
    culture = Culture(name="Ячмень", kind="main", forage_flag=False, crop_group="зерновые")
    db_session.add(culture)
    db_session.flush()
    cycle = CropCycle(field_id=field.id, year=2026, culture_id=culture.id, kind="main")
    db_session.add(cycle)
    input_ = AgroInput(kind="ppp", name="Препарат Х", unit="l_ha", waiting_days=20)
    db_session.add(input_)
    db_session.flush()
    db_session.add(
        TreatmentPlan(cycle_id=cycle.id, input_id=input_.id, dose=2.0, operation_type="spraying")
    )
    operation = FieldOperation(
        field_id=field.id,
        operation_type="spraying",
        date=date(2026, 6, 10),
        actual_area_ga=10.0,
        source="manual",
        status="done",
    )
    db_session.add(operation)
    machine = Machine(machine_type="tractor", model="К-744")
    db_session.add(machine)
    db_session.flush()
    db_session.add(
        MachineJob(
            machine_id=machine.id,
            performed_at=date(2026, 6, 10),
            area_ha=10.0,
            source="manual",
            status="confirmed",
        )
    )
    db_session.add(Alert(type="weather_danger", severity="warning", field_id=field.id, payload={}))
    db_session.commit()
    return {"field": field}


class TestFormats:
    def test_json_envelope_and_geojson_object(self, client, db_session, manager_headers, demo_data):
        resp = client.get(
            API, params={"entity": "fields", "format": "json"}, headers=manager_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"items", "total", "limit", "offset"}
        assert body["total"] == 1
        item = body["items"][0]
        assert item["name"] == "Экспортное"
        assert isinstance(item["area_ga"], float)
        assert item["contour"]["type"] == "Polygon"  # GeoJSON-объект, не строка
        assert item["status"] == "active"

    def test_csv_bom_delimiter_and_geojson_string(
        self, client, db_session, manager_headers, demo_data
    ):
        resp = client.get(
            API, params={"entity": "fields", "format": "csv"}, headers=manager_headers
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "export_fields_" in resp.headers["content-disposition"]
        text = resp.text
        assert text.startswith("﻿")  # BOM — Excel открывает UTF-8
        header_line = text.splitlines()[0].lstrip("﻿")
        columns = header_line.split(";")
        assert "name" in columns and "area_ga" in columns and "contour" in columns
        data_line = text.splitlines()[1]
        assert '"type": "Polygon"' in data_line or '"type":"Polygon"' in data_line

    def test_json_dates_iso_and_decimal(self, client, db_session, manager_headers, demo_data):
        resp = client.get(API, params={"entity": "operations"}, headers=manager_headers)
        item = resp.json()["items"][0]
        assert item["date"] == "2026-06-10"
        assert item["actual_area_ga"] == 10.0
        assert isinstance(item["field_id"], str)  # UUID → строка

    @pytest.mark.parametrize("entity", ALL_ENTITIES)
    @pytest.mark.parametrize("fmt", ["json", "csv"])
    def test_every_entity_both_formats(
        self, client, db_session, manager_headers, demo_data, entity, fmt
    ):
        resp = client.get(API, params={"entity": entity, "format": fmt}, headers=manager_headers)
        assert resp.status_code == 200, resp.text
        if fmt == "json":
            assert resp.json()["total"] == 1


class TestEdgeCases:
    def test_empty_result(self, client, db_session, manager_headers):
        resp = client.get(
            API, params={"entity": "alerts", "format": "json"}, headers=manager_headers
        )
        body = resp.json()
        assert body["items"] == [] and body["total"] == 0
        resp = client.get(
            API, params={"entity": "alerts", "format": "csv"}, headers=manager_headers
        )
        assert resp.text.count("\n") == 1  # только строка заголовка

    def test_unknown_entity_400(self, client, db_session, manager_headers):
        resp = client.get(API, params={"entity": "users"}, headers=manager_headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == "unknown_entity"

    def test_unknown_format_400(self, client, db_session, manager_headers, demo_data):
        resp = client.get(
            API, params={"entity": "fields", "format": "xml"}, headers=manager_headers
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "invalid_format"

    def test_missing_entity_400(self, client, db_session, manager_headers):
        resp = client.get(API, headers=manager_headers)
        assert resp.status_code == 400  # обязательный параметр


class TestRbac:
    def test_manager_ok_admin_ok(self, client, db_session, demo_data):
        make_user(db_session, login="root_ex", role=UserRole.ADMIN)
        headers = auth_header(client, "root_ex", "secret123")
        assert client.get(API, params={"entity": "fields"}, headers=headers).status_code == 200

    def test_agronomist_403(self, client, db_session, demo_data):
        make_user(db_session, login="agro_ex", role=UserRole.AGRONOMIST)
        headers = auth_header(client, "agro_ex", "secret123")
        resp = client.get(API, params={"entity": "fields"}, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "insufficient_role"

    def test_401_without_token(self, client, db_session):
        assert client.get(API, params={"entity": "fields"}).status_code == 401
