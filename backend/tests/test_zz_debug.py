"""Временный отладочный тест (удалить)."""

import uuid

from app.core.rbac import group_checker
from app.models.auth import User, UserRole
from tests.test_auth import auth_header, make_user

API = "/api/v1"


def test_dbg_rbac_direct(client, db_session):
    user = make_user(db_session, login="mechdbg", role=UserRole.MACHINE_OPERATOR)
    db_session.refresh(user)
    print("ROLE IN DB:", repr(user.role), type(user.role))
    check = group_checker("sources")
    try:
        result = check(user)
        print("CHECK RETURNED:", result)
    except Exception as exc:  # noqa: BLE001
        print("CHECK RAISED:", type(exc).__name__, exc)

    h = auth_header(client, "mechdbg", "secret123")
    resp = client.get(f"{API}/sources/status", headers=h)
    print("STATUS:", resp.status_code)
    # и через алерты (группа all) для контраста
    print("ALERTS:", client.get(f"{API}/alerts", headers=h).status_code)
