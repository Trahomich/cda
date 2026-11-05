"""Аутентификация: POST /auth/login и GET /auth/me (задача MVP-4)."""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import create_access_token, verify_password
from app.models.auth import AuditRecord, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=100, description="Логин пользователя")
    password: str = Field(min_length=1, max_length=200, description="Пароль")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: uuid.UUID
    login: str
    fio: str
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


@router.post("/login", response_model=TokenResponse, summary="Вход: логин и пароль → JWT")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
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
    return TokenResponse(access_token=create_access_token(user_id=user.id, role=user.role))


@router.get("/me", response_model=MeResponse, summary="Профиль текущего пользователя")
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(user_id=user.id, login=user.login, fio=user.fio, role=user.role)
