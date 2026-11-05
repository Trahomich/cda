"""Точка входа FastAPI: приложение, /healthz, планировщик погоды, ошибки."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Старт/остановка фоновых подсистем (опрос погоды и каталога Sentinel-2)."""
    schedulers = []
    try:
        # импорт внутри lifespan: не тянуть APScheduler в тесты без необходимости
        from app.modules.satellite.scheduler import create_satellite_scheduler
        from app.modules.weather.scheduler import create_scheduler

        weather = create_scheduler(get_settings())
        if weather is not None:
            schedulers.append(weather)
        satellite = create_satellite_scheduler(get_settings())
        if satellite is not None:
            schedulers.append(satellite)
        for scheduler in schedulers:
            scheduler.start()
    except Exception as exc:  # noqa: BLE001 — сбой планировщика не должен ронять API
        import logging

        logging.getLogger(__name__).warning("планировщики не запущены: %s", exc)
    yield
    for scheduler in schedulers:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    """Фабрика приложения (используется тестами и uvicorn)."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/healthz", tags=["service"], summary="Проверка живости")
    def healthz() -> dict[str, str]:
        """Liveness-проба: отвечает 200, пока работает процесс API."""
        return {"status": "ok"}

    return app


app = create_app()
