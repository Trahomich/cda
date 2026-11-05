"""Тесты модуля полей (задача MVP-5, API-01..05).

Проверяется: CRUD по контракту 06 §5, версии контуров с пересчётом площади,
гео-валидация (замкнутость, самопересечение, минимальная площадь — PostGIS),
точность площади 2 % (REQ-F-001), автомат статусов с аудитом, импорт GeoJSON,
RBAC (чтение — группа fields, изменение — agronomist/admin). Тестовый контур —
PostgreSQL+PostGIS (DATABASE_URL_TEST, паттерн #20).
"""

import uuid
from datetime import date

from app.models.agronomy import CropCycle
from app.models.auth import AuditRecord, UserRole
from app.models.references import Culture
from sqlalchemy import select

from tests.test_auth import auth_header, make_user

API = "/api/v1/fields"


def square(lon0: float, lat0: float, d_lon: float, d_lat: float) -> dict:
    """GeoJSON Polygon: квадрат (запад-юг → восток → север → назад), CCW."""
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon0, lat0],
                [lon0 + d_lon, lat0],
                [lon0 + d_lon, lat0 + d_lat],
                [lon0, lat0 + d_lat],
                [lon0, lat0],
            ]
        ],
    }


# Эталонный полигон на экваторе: 0,01° × 0,01° ≈ 1113,19 м × 1105,74 м
# → ожидаемая геодезическая площадь ≈ 123,09 га (проверка REQ-F-001: 2 %)
REF_POLYGON = square(40.0, 0.0, 0.01, 0.01)
REF_AREA_GA = 123.09
REF_TOLERANCE = 0.02


def agro_headers(client, db_session) -> dict:
    make_user(db_session, login="agro_f", role=UserRole.AGRONOMIST)
    return auth_header(client, "agro_f", "secret123")


def create_field(client, headers, *, name="Степное-1", contour=None) -> dict:
    resp = client.post(
        API, json={"name": name, "contour": contour or REF_POLYGON}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreateField:
    def test_401_without_token(self, client, db_session):
        resp = client.post(API, json={"name": "X", "contour": REF_POLYGON})
        assert resp.status_code == 401
        assert resp.json()["code"] == "invalid_token"

    def test_create_201_with_location_and_version1(self, client, db_session):
        headers = agro_headers(client, db_session)
        resp = client.post(
            API,
            json={"name": "Степное-7", "contour": REF_POLYGON, "reason": "импорт GeoJSON"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers["location"].startswith(f"{API}/")
        body = resp.json()
        assert body["name"] == "Степное-7"
        assert body["status"] == "active"
        assert body["contour"]["type"] == "Polygon"
        assert len(body["versions"]) == 1
        assert body["versions"][0]["version_no"] == 1
        assert body["versions"][0]["reason"] == "импорт GeoJSON"

    def test_area_within_2_percent_of_reference(self, client, db_session):
        """REQ-F-001: площадь ST_Area(geography) совпадает с эталоном в 2 %."""
        headers = agro_headers(client, db_session)
        body = create_field(client, headers, name="Эталон")
        assert abs(body["area_ga"] - REF_AREA_GA) / REF_AREA_GA < REF_TOLERANCE

    def test_duplicate_name_409(self, client, db_session):
        headers = agro_headers(client, db_session)
        create_field(client, headers, name="Дубль")
        resp = client.post(API, json={"name": "Дубль", "contour": REF_POLYGON}, headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "duplicate_name"

    def test_not_polygon_422(self, client, db_session):
        headers = agro_headers(client, db_session)
        resp = client.post(
            API,
            json={"name": "Бэд", "contour": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_geometry"

    def test_unclosed_ring_422(self, client, db_session):
        contour = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0.05, 0.05]]],
        }
        resp = client.post(API, json={"name": "Бэд", "contour": contour},
                           headers=agro_headers(client, db_session))
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_geometry"

    def test_self_intersection_422(self, client, db_session):
        # «бабочка»: кольцо пересекает само себя
        contour = {
            "type": "Polygon",
            "coordinates": [
                [
                    [0.0, 0.0],
                    [0.01, 0.01],
                    [0.0, 0.01],
                    [0.01, 0.0],
                    [0.0, 0.0],
                ]
            ],
        }
        resp = client.post(API, json={"name": "Бэд", "contour": contour},
                           headers=agro_headers(client, db_session))
        assert resp.status_code == 422
        assert resp.json()["code"] == "self_intersection"

    def test_area_below_min_422(self, client, db_session):
        tiny = square(40.0, 0.0, 0.0002, 0.0002)  # ≈ 0,05 га < 0,1 га
        resp = client.post(API, json={"name": "Мелкое", "contour": tiny},
                           headers=agro_headers(client, db_session))
        assert resp.status_code == 422
        assert resp.json()["code"] == "area_below_min"


class TestListAndGet:
    def test_list_pagination_and_filters(self, client, db_session):
        headers = agro_headers(client, db_session)
        create_field(client, headers, name="Альфа")
        create_field(client, headers, name="Бета")
        resp = client.get(API, params={"limit": 1, "offset": 0}, headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2 and body["limit"] == 1 and body["offset"] == 0
        assert len(body["items"]) == 1
        assert body["items"][0]["field_id"]
        assert body["items"][0]["current_cycle"] is None
        assert "contour" not in body["items"][0] or body["items"][0]["contour"] is None

        resp = client.get(API, params={"q": "альф"}, headers=headers)
        assert [i["name"] for i in resp.json()["items"]] == ["Альфа"]

        resp = client.get(API, params={"status": "archived"}, headers=headers)
        assert resp.json()["total"] == 0

    def test_list_with_geometry_adds_contour(self, client, db_session):
        headers = agro_headers(client, db_session)
        create_field(client, headers, name="Гео")
        resp = client.get(API, params={"with_geometry": "true"}, headers=headers)
        assert resp.json()["items"][0]["contour"]["type"] == "Polygon"

    def test_list_current_cycle_aggregate(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="С циклом")["field_id"]
        culture = Culture(name="Пшеница озимая", kind="main", forage_flag=False, crop_group="зерновые")
        db_session.add(culture)
        db_session.flush()
        db_session.add(CropCycle(field_id=uuid.UUID(field_id), year=date.today().year,
                                 culture_id=culture.id, kind="main"))
        db_session.commit()
        resp = client.get(API, headers=headers)
        cycle = resp.json()["items"][0]["current_cycle"]
        assert cycle == {
            "year": date.today().year,
            "culture_name": "Пшеница озимая",
            "kind": "main",
        }

    def test_get_detail_404(self, client, db_session):
        headers = agro_headers(client, db_session)
        resp = client.get(f"{API}/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_detail_card_shape(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers)["field_id"]
        resp = client.get(f"{API}/{field_id}", headers=headers)
        body = resp.json()
        assert body["field_id"] == field_id
        assert body["contour"]["type"] == "Polygon"
        assert len(body["versions"]) == 1
        assert body["operations"] == []
        assert body["ndvi"] is None and body["weather"] is None


class TestPatchAndVersions:
    def test_patch_rename(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="Прежнее")["field_id"]
        resp = client.patch(f"{API}/{field_id}", json={"name": "Новое"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Новое"

    def test_patch_contour_creates_version_and_recalculates_area(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="Версионное")["field_id"]
        bigger = square(40.0, 0.0, 0.02, 0.02)  # в 4 раза больше эталона
        resp = client.patch(
            f"{API}/{field_id}", json={"contour": bigger, "reason": "уточнили по GPS"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["versions"]) == 2
        assert body["versions"][1]["reason"] == "уточнили по GPS"
        assert body["versions"][1]["area_ga"] > body["versions"][0]["area_ga"]
        assert body["area_ga"] == body["versions"][1]["area_ga"]

    def test_post_version_endpoint(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="Через версии")["field_id"]
        resp = client.post(
            f"{API}/{field_id}/versions",
            json={"contour": square(40.0, 0.0, 0.03, 0.02), "reason": "новый обмер"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.headers["location"].endswith("/versions/2")
        assert resp.json()["version_no"] == 2

        resp = client.get(f"{API}/{field_id}/versions", headers=headers)
        body = resp.json()
        assert body["total"] == 2
        assert [v["version_no"] for v in body["items"]] == [1, 2]

    def test_status_archive_with_reason_and_audit(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="В архив")["field_id"]
        resp = client.patch(
            f"{API}/{field_id}", json={"status": "archived", "reason": "объединили хозяйства"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"
        rec = db_session.scalar(
            select(AuditRecord).where(AuditRecord.action == "status_change")
        )
        assert rec is not None
        assert rec.changed["status_from"] == "active"
        assert rec.changed["status_to"] == "archived"
        assert rec.changed["reason"] == "объединили хозяйства"

    def test_status_change_without_reason_400(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers)["field_id"]
        resp = client.patch(f"{API}/{field_id}", json={"status": "archived"}, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == "reason_required"

    def test_terminal_status_transition_422(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="Терминальное")["field_id"]
        client.patch(f"{API}/{field_id}", json={"status": "archived", "reason": "x"}, headers=headers)
        resp = client.patch(
            f"{API}/{field_id}", json={"status": "active", "reason": "вернём"}, headers=headers
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_status_transition"

    def test_merge_transition_allowed(self, client, db_session):
        headers = agro_headers(client, db_session)
        field_id = create_field(client, headers, name="Слива")["field_id"]
        resp = client.patch(
            f"{API}/{field_id}", json={"status": "merged", "reason": "слияние с соседним"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "merged"


class TestImport:
    def test_import_report(self, client, db_session):
        headers = agro_headers(client, db_session)
        create_field(client, headers, name="Уже есть")
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Импорт-1"},
                    "geometry": REF_POLYGON,
                },
                {
                    "type": "Feature",
                    "properties": {"name": "Уже есть"},  # дубль → skipped
                    "geometry": REF_POLYGON,
                },
                {
                    "type": "Feature",
                    "properties": {"name": "Без геометрии"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                },
                {"type": "Feature", "properties": {}, "geometry": REF_POLYGON},
            ],
        }
        resp = client.post(
            f"{API}/import",
            files={"file": ("fields.geojson", __import__("json").dumps(fc), "application/geo+json")},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        report = resp.json()
        assert report["created"] == 1
        assert report["skipped"] == 1
        assert len(report["errors"]) == 2
        assert {e["row"] for e in report["errors"]} == {3, 4}

    def test_import_bad_json_422(self, client, db_session):
        headers = agro_headers(client, db_session)
        resp = client.post(
            f"{API}/import",
            files={"file": ("fields.geojson", "not json", "application/json")},
            headers=headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_geojson"


class TestRbac:
    def test_machine_operator_can_read_not_write(self, client, db_session):
        make_user(db_session, login="oper", role=UserRole.MACHINE_OPERATOR)
        headers = auth_header(client, "oper", "secret123")
        assert client.get(API, headers=headers).status_code == 200
        resp = client.post(API, json={"name": "X", "contour": REF_POLYGON}, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "insufficient_role"

    def test_manager_can_read_not_write(self, client, db_session):
        make_user(db_session, login="boss_f", role=UserRole.MANAGER)
        headers = auth_header(client, "boss_f", "secret123")
        assert client.get(API, headers=headers).status_code == 200
        assert client.post(API, json={"name": "X", "contour": REF_POLYGON},
                           headers=headers).status_code == 403

    def test_livestock_specialist_forbidden(self, client, db_session):
        make_user(db_session, login="zoo", role=UserRole.LIVESTOCK_SPECIALIST)
        headers = auth_header(client, "zoo", "secret123")
        assert client.get(API, headers=headers).status_code == 403

    def test_admin_can_write(self, client, db_session):
        make_user(db_session, login="root_f", role=UserRole.ADMIN)
        headers = auth_header(client, "root_f", "secret123")
        resp = client.post(API, json={"name": "Админское", "contour": REF_POLYGON},
                           headers=headers)
        assert resp.status_code == 201
