"""Хеширование паролей (argon2id) и выпуск/проверка JWT (MVP-4)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings
from app.models.auth import UserRole

_password_hasher = PasswordHasher()  # argon2id — параметры по умолчанию OWASP-класс


def hash_password(password: str) -> str:
    """Хеш пароля для записи нового пользователя / смены пароля."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверка пароля по хешу; любые ошибки argon2 = неверный пароль."""
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # повреждённый/чужой формат хеша — не отдаём деталей наружу
        return False

def create_access_token(*, user_id: uuid.UUID, role: UserRole) -> str:
    """JWT HS256: sub = id пользователя, role, iat/exp, jti (для отзыва в будущем)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Декодирование и проверка подписи/срока; ошибки — jwt.PyJWTError."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
