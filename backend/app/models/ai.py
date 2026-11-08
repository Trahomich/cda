"""Модели ИИ-блока (docs/09-ai.md §9, задача #119).

Схема создаётся миграцией 0018_ai_field_advice; модели — зеркало миграции.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AIFieldAdvice(Base):
    """AI-рекомендация «что делать сейчас» для поля (09-ai §9.3).

    История — записи со status ok/fallback (empty не пишем: пустой сезон
    advice не получает). Ретеншн не ограничен (~1 запись/поле/день).
    """

    __tablename__ = "ai_field_advice"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'fallback', 'empty')", name="ck_ai_field_advice_status"),
        CheckConstraint(
            "provider IN ('anthropic', 'ollama')", name="ck_ai_field_advice_provider"
        ),
        Index("ix_ai_field_advice_field_created", "field_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("dat_field.id", name="fk_ai_field_advice_field"), nullable=False
    )
    #: момент генерации (09 §9.3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: напр. glm-5.3-flash / qwen3.5:9b
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    #: anthropic / ollama
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    #: дата «настоящего момента» (09 §9.4): advice_date = дата генерации
    advice_date: Mapped[date] = mapped_column(Date, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    #: sha256 сериализованного context (идемпотентность CLI-пересчёта, §9.4)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ok — ответ LLM прошёл guardrail; fallback — конкатенация items §8.2
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="ok")
