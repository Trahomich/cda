"""Роутер /api/v1 (CONTRACTS.md §8).

Эндпоинты модулей подключаются сюда по мере реализации MVP:
auth, fields, rotation, inputs, weather, satellite, machinery.
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.modules.auth import router as auth_router
from app.modules.fields import router as fields_router
from app.modules.weather import router as weather_router

settings = get_settings()

api_router = APIRouter(prefix=settings.api_v1_prefix)
api_router.include_router(auth_router)
api_router.include_router(fields_router)
api_router.include_router(weather_router)
