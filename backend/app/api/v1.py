"""Роутер /api/v1 (CONTRACTS.md §8).

Эндпоинты модулей подключаются сюда по мере реализации MVP:
fields, rotation, inputs, weather, satellite, machinery.
"""

from fastapi import APIRouter

from app.core.config import get_settings

settings = get_settings()

api_router = APIRouter(prefix=settings.api_v1_prefix)
