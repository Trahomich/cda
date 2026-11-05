"""Точка входа FastAPI: приложение, /healthz, глобальные обработчики ошибок."""

from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers

settings = get_settings()


def create_app() -> FastAPI:
    """Фабрика приложения (используется тестами и uvicorn)."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Домашние / внешние клиенты получают ошибки строго в RFC 7807.
        exception_handlers=None,
    )
    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/healthz", tags=["service"], summary="Проверка живости")
    def healthz() -> dict[str, str]:
        """Liveness-проба: отвечает 200, пока работает процесс API."""
        return {"status": "ok"}

    return app


app = create_app()
