"""Аутентификация: API-46 POST /auth/login и API-47 GET /auth/me (задача MVP-4).

Формат ответов зафиксирован в docs/06-data-api.md §5:
- API-46: {login, password} → {token, user {name, role, farm_name}}, 401 bad_credentials;
- API-47: {id, name, role}.
Роли — значения enum UserRole (01-requirements §2).
"""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import create_access_token, verify_password
from app.models.auth import AuditRecord, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=100, description="Логин пользователя")
    password: str = Field(min_length=1, max_length=200, description="Пароль")


class UserBrief(BaseModel):
    name: str = Field(description="ФИО пользователя")
    role: UserRole
    farm_name: str = Field(description="Наименование хозяйства (настройка FARM_NAME)")


class LoginResponse(BaseModel):
    token: str
    user: UserBrief


class MeResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: UserRole


def _audit_login(db: Session, *, user_id: uuid.UUID | None, login: str, success: bool) -> None:
    """Аудит входов в dat_audit (06 §4); действие расширено на login/login_failed."""
    db.add(
        AuditRecord(
            entity="user",
            entity_id=user_id if user_id is not None else uuid.uuid4(),  # NOT NULL в схеме
            user_id=user_id,
            action="login" if success else "login_failed",
            changed={"login": login},
        )
    )
    db.commit()


@router.post("/login", response_model=LoginResponse, summary="API-46: вход, логин и пароль → JWT")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.scalar(select(User).where(User.login == body.login))
    ok = user is not None and user.is_active and verify_password(body.password, user.password_hash)
    if not ok:
        _audit_login(db, user_id=user.id if user else None, login=body.login, success=False)
        # единый ответ и для неизвестного логина, и для неверного пароля
        raise AppError(
            "Неверный логин или пароль",
            code="bad_credentials",
            title="Не авторизован",
            status_code=401,
        )

    _audit_login(db, user_id=user.id, login=body.login, success=True)
    return LoginResponse(
        token=create_access_token(user_id=user.id, role=user.role),
        user=UserBrief(
            name=user.fio, role=user.role, farm_name=get_settings().farm_name
        ),
    )


@router.get("/me", response_model=MeResponse, summary="API-47: текущий пользователь по JWT")
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(id=user.id, name=user.fio, role=user.role)
