"""Общие зависимости API: сессия БД и текущий пользователь (MVP-4)."""

import uuid
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.models.auth import User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Текущий пользователь из JWT (Authorization: Bearer <token>).

    Ошибки: 401 invalid_token (нет токена/битый/истёк) и 401 inactive_user.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            "Требуется заголовок Authorization: Bearer <token>",
            code="invalid_token",
            title="Не авторизован",
            status_code=401,
        )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (pyjwt.PyJWTError, KeyError, ValueError) as exc:
        raise AppError(
            f"Недействительный токен: {exc}",
            code="invalid_token",
            title="Не авторизован",
            status_code=401,
        ) from exc

    user = db.get(User, user_id)
    if user is None:
        raise AppError(
            "Пользователь токена не найден",
            code="invalid_token",
            title="Не авторизован",
            status_code=401,
        )
    if not user.is_active:
        raise AppError(
            "Пользователь деактивирован",
            code="inactive_user",
            title="Не авторизован",
            status_code=401,
        )
    return user


# Аннотация для эндпоинтов: `user: CurrentUser` (FastAPI подставит Depends).
CurrentUser = Annotated[User, Depends(get_current_user)]
