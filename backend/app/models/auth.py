"""Модели аутентификации: пользователи (dat_user) и аудит (dat_audit).

Схема dat_audit — по docs/06-data-api.md §4: `id, entity, entity_id, user_id,
action (create/update/status_change), changed JSONB, at`. Для аудита входов
действие расширено значениями `login` / `login_failed` (задача MVP-4).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(enum.StrEnum):
    """Роли системы — docs/01-requirements.md §2, матрица прав 07-ux §5."""

    ADMIN = "admin"
    AGRONOMIST = "agronomist"
    MANAGER = "manager"
    MACHINE_OPERATOR = "machine_operator"
    LIVESTOCK_SPECIALIST = "livestock_specialist"


class User(Base):
    """Пользователь ЦДА (таблица dat_user, MVP-4).

    Пароли хранятся только хешем argon2id (REQ-NF-005).
    """

    __tablename__ = "dat_user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fio: Mapped[str] = mapped_column(String(200), nullable=False)
    login: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False, length=40),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditRecord(Base):
    """Запись аудита критичных действий (таблица dat_audit, 06 §4)."""

    __tablename__ = "dat_audit"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("dat_user.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    changed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
