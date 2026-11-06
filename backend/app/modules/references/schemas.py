"""Схемы ответов справочников (v1-08 #54)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CultureItem(BaseModel):
    """Культура справочника (ref_culture), API-совместимо с docs/06 §3."""

    model_config = ConfigDict(from_attributes=True)

    culture_id: uuid.UUID
    name: str = Field(max_length=100)
    kind: str = Field(max_length=10)
    forage_flag: bool
    crop_group: str = Field(max_length=50)
    repeat_years: int
    color_hex: str | None = Field(default=None, max_length=7, pattern=r"^#[0-9a-fA-F]{6}$")
    updated_at: datetime


class CultureListResponse(BaseModel):
    items: list[CultureItem]
    total: int
    limit: int
    offset: int
