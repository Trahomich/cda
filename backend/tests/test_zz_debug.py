"""Временный отладочный тест (удалить)."""

from app.models.auth import UserRole
from tests.test_auth import auth_header, make_user

API = "/api/v1"


def test_dbg_sources_rbac(client, db_session):
    make_user(db_session, login="mechdbg", role=UserRole.MACHINE_OPERATOR)
    h = auth_header(client, "mechdbg", "secret123")
    resp = client.get(f"{API}/sources/status", headers=h)
    print("STATUS:", resp.status_code)
    print("BODY:", resp.text[:300])
    print("HEADERS:", dict(resp.headers))
    resp2 = client.get(f"{API}/alerts", headers=h)
    print("ALERTS:", resp2.status_code)
