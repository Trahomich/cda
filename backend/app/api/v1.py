"""Роутер /api/v1 (CONTRACTS.md §8).

Эндпоинты модулей подключаются сюда по мере реализации MVP:
auth, fields, rotation, inputs, weather, satellite, machinery.
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.modules.auth import router as auth_router
from app.modules.alerts import router as alerts_router, sources_router
from app.modules.export import router as export_router
from app.modules.fields import router as fields_router
from app.modules.inputs import router as inputs_router
from app.modules.machinery import router as machinery_router
from app.modules.operations.router import router as operations_router
from app.modules.rotation import router as rotation_router
from app.modules.satellite import router as satellite_router
from app.modules.weather import router as weather_router

settings = get_settings()

api_router = APIRouter(prefix=settings.api_v1_prefix)
api_router.include_router(auth_router)
api_router.include_router(fields_router)
api_router.include_router(inputs_router)
api_router.include_router(export_router)
api_router.include_router(weather_router)
api_router.include_router(machinery_router)
api_router.include_router(rotation_router)
api_router.include_router(operations_router)
api_router.include_router(satellite_router)
api_router.include_router(alerts_router)
api_router.include_router(sources_router)
