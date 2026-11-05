"""Тесты аутентификации и RBAC (задача MVP-4).

Проверяется: логин выдаёт JWT, неверный пароль → 401 bad_credentials (RFC 7807),
/me требует токен, 403 при недостаточной роли, аудит входов, CLI create-admin.
"""

import uuid

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.core.db import get_db
from app.core.rbac import API_GROUP_ROLES, role_checker
from app.core.security import hash_password
from app.main import create_app
from app.models.auth import AuditRecord, User, UserRole


def make_user(db, *, login: str, role: UserRole, password: str = "secret123",
              is_active: bool = True) -> User:
    user = User(
        fio=f"Тест {login}",
        login=login,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    return user


def auth_header(client: TestClient, login: str, password: str) -> dict:
    resp = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestLogin:
    def test_login_ok_returns_jwt_and_audits(self, client, db_session):
        make_user(db_session, login="agro1", role=UserRole.AGRONOMIST, password="pw-agro-1")
        resp = client.post(
            "/api/v1/auth/login", json={"login": "agro1", "password": "pw-agro-1"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"].count(".") == 2  # compact JWS: header.payload.signature
        # аудит успешного входа
        rec = db_session.scalar(
            select(AuditRecord).where(AuditRecord.action == "login")
        )
        assert rec is not None and rec.entity == "user" and rec.changed == {"login": "agro1"}

    def test_wrong_password_401_rfc7807(self, client, db_session):
        make_user(db_session, login="agro2", role=UserRole.AGRONOMIST, password="right")
        resp = client.post(
            "/api/v1/auth/login", json={"login": "agro2", "password": "wrong"}
        )
        assert resp.status_code == 401
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert resp.json()["code"] == "bad_credentials"
        rec = db_session.scalar(
            select(AuditRecord).where(AuditRecord.action == "login_failed")
        )
        assert rec is not None and rec.changed == {"login": "agro2"}

    def test_unknown_login_same_401(self, client, db_session):
        resp = client.post(
            "/api/v1/auth/login", json={"login": "ghost", "password": "whatever"}
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "bad_credentials"  # тот же код, что при неверном пароле

    def test_inactive_user_cannot_login(self, client, db_session):
        make_user(db_session, login="off", role=UserRole.AGRONOMIST, is_active=False)
        resp = client.post("/api/v1/auth/login", json={"login": "off", "password": "secret123"})
        assert resp.status_code == 401


class TestMe:
    def test_me_returns_profile(self, client, db_session):
        user = make_user(db_session, login="boss", role=UserRole.MANAGER)
        headers = auth_header(client, "boss", "secret123")
        resp = client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "boss"
        assert body["role"] == "manager"
        assert uuid.UUID(body["user_id"]) == user.id

    def test_me_without_token_401(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert resp.json()["code"] == "invalid_token"

    def test_me_with_garbage_token_401(self, client):
        resp = client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "invalid_token"


class TestRbac:
    @pytest.fixture
    def rbac_client(self, client, db_session):
        """Приложение с тестовым роутером, защищённым role_checker (как модуль)."""
        admin_only = APIRouter(prefix="/api/v1/_test_admin", tags=["test"])

        @admin_only.get("", response_model=None)
        def secret(user: User = Depends(role_checker(UserRole.ADMIN))):
            return {"ok": True, "role": user.role.value}

        app = client.app
        app.include_router(admin_only)
        return client

    def test_403_for_wrong_role(self, rbac_client, db_session):
        make_user(db_session, login="mech1", role=UserRole.MACHINE_OPERATOR)
        resp = rbac_client.get(
            "/api/v1/_test_admin", headers=auth_header(rbac_client, "mech1", "secret123")
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "insufficient_role"

    def test_200_for_allowed_role(self, rbac_client, db_session):
        make_user(db_session, login="root1", role=UserRole.ADMIN)
        resp = rbac_client.get(
            "/api/v1/_test_admin", headers=auth_header(rbac_client, "root1", "secret123")
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    def test_group_mapping_matches_07ux_matrix(self):
        """Выборочные сверки API_GROUP_ROLES с матрицей прав 07-ux §5."""
        roles = lambda g: {r.value for r in API_GROUP_ROLES[g]}  # noqa: E731
        all5 = {"admin", "agronomist", "manager", "machine_operator", "livestock_specialist"}
        # E-10 Погода и алерты: все 5 ролей
        assert roles("weather") == all5
        # E-16 Пользователи и права: только админ
        assert roles("users") == {"admin"}
        # E-09 Спутниковый мониторинг: А, Р, Д
        assert roles("satellite") == {"admin", "agronomist", "manager"}
        # E-07/E-08: А, Р, М, Д (без зоотехника)
        assert roles("operations") == {"admin", "agronomist", "manager", "machine_operator"}
        # E-15 Справочники: А, Р, З, Д
        assert roles("references") == {
            "admin", "agronomist", "livestock_specialist", "manager",
        }


class TestCliCreateAdmin:
    def test_create_admin(self, client, db_session, monkeypatch):
        monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
        rc = cli_main(["create-admin", "--login", "admin", "--password", "s3cret!"])
        assert rc == 0
        user = db_session.scalar(select(User).where(User.login == "admin"))
        assert user is not None and user.role == UserRole.ADMIN and user.is_active
        # созданный админ может войти
        resp = client.post("/api/v1/auth/login", json={"login": "admin", "password": "s3cret!"})
        assert resp.status_code == 200

    def test_create_admin_idempotent(self, db_session, monkeypatch):
        monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
        assert cli_main(["create-admin", "--login", "admin", "--password", "x1"]) == 0
        assert cli_main(["create-admin", "--login", "admin", "--password", "x2"]) == 1


class TestHealthStillOk:
    """Каркас MVP-1 не сломан: /healthz жив без авторизации."""

    def test_healthz(self, client):
        assert client.get("/healthz").status_code == 200
