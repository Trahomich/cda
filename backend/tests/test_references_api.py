"""Тесты API справочника культур (v1-08 #54): GET /references/cultures.

Проверяется: отдача color_hex, фильтры crop_group/q, пагинация, RBAC
(группа references: агроном/руководитель/зоотехник/админ — 200, аноним — 401).
Тестовый контур — PostgreSQL (DATABASE_URL_TEST, паттерн #20).
"""

from app.models.auth import UserRole
from app.seed import CULTURES
from app.seed import seed_references
from tests.test_auth import auth_header, make_user

API = "/api/v1/references/cultures"


def hdr(client, db_session, *, login="agro_r", role=UserRole.AGRONOMIST) -> dict:
    make_user(db_session, login=login, role=role)
    return auth_header(client, login, "secret123")


def test_cultures_list_returns_seeded_color_hex(client, db_session) -> None:
    seed_references(db_session)
    resp = client.get(API, headers=hdr(client, db_session))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 20
    items = {i["name"]: i for i in body["items"]}
    assert len(items) == body["total"]  # без дубликатов
    # каждая сид-культура отдаётся со стабильным color_hex
    for c in CULTURES:
        assert c["name"] in items
        assert items[c["name"]]["color_hex"] == c["color_hex"]
        assert items[c["name"]]["crop_group"] == c["crop_group"]
        assert items[c["name"]]["repeat_years"] == c.get("repeat_years", 3)


def test_cultures_filter_and_pagination(client, db_session) -> None:
    seed_references(db_session)
    h = hdr(client, db_session, login="agro_r2")
    resp = client.get(API, params={"crop_group": "legumes"}, headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == sum(1 for c in CULTURES if c["crop_group"] == "legumes")
    assert all(i["crop_group"] == "legumes" for i in body["items"])

    resp = client.get(API, params={"q": "пшеница"}, headers=h)
    names = [i["name"] for i in resp.json()["items"]]
    assert names and all("пшеница" in n.lower() for n in names)

    page = client.get(API, params={"limit": 5, "offset": 5}, headers=h).json()
    assert len(page["items"]) == 5
    assert page["offset"] == 5


def test_cultures_requires_auth(client, db_session) -> None:
    resp = client.get(API)  # без токена
    assert resp.status_code == 401
