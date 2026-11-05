"""Единый формат ошибок API — RFC 7807 (CONTRACTS.md §8).

Все ответы об ошибках отдаются с Content-Type: application/problem+json
и содержат поля: type, title, status, detail, instance + код приложения `code`.
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# type URI ошибок: about:blank значит «без дополнительной семантики»
BLANK_TYPE = "about:blank"


class Problem(BaseModel):
    """Тело ошибки по RFC 7807 с расширением `code` (код приложения)."""

    type: str = BLANK_TYPE
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str | None = None


class AppError(Exception):
    """Базовая ошибка приложения.

    Доменные модули наследуют её: `code` — машиночитаемый код,
    `status` — HTTP-код по CONTRACTS.md §8 (400/404/409/422/500).
    `extra` — доменные расширения тела problem+json (например,
    rule_id/severity/rationale для нарушений бизнес-правил,
    docs/06 §5 примеры ошибок).
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    title: str = "Некорректный запрос"
    code: str = "bad_request"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        title: str | None = None,
        status_code: int | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if title is not None:
            self.title = title
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra


def problem_response(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str | None,
    code: str | None = None,
    extra: dict[str, str] | None = None,
) -> JSONResponse:
    body = Problem(
        status=status_code,
        title=title,
        detail=detail,
        instance=str(request.url.path),
        code=code,
    ).model_dump(exclude_none=True)
    if extra:
        # доменные расширения RFC 7807 (rule_id, severity, rationale, ...)
        body.update(extra)
    return JSONResponse(
        status_code=status_code, content=body, media_type="application/problem+json"
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Глобальные обработчики: все ошибки API — application/problem+json."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return problem_response(
            request,
            status_code=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            code=exc.code,
            extra=getattr(exc, "extra", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Ошибки валидации запроса — 400 (422 по контракту зарезервирован
        # под нарушение бизнес-правила, а не под синтаксис запроса).
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return problem_response(
            request,
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Некорректный запрос",
            detail=detail,
            code="validation_error",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem_response(
            request,
            status_code=exc.status_code,
            title=HTTP_STATUS_TITLES.get(exc.status_code, "Ошибка"),
            detail=detail,
            code=f"http_{exc.status_code}",
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Внутренняя ошибка: без деталей наружу (логирует uvicorn/starlette).
        return problem_response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Внутренняя ошибка сервера",
            detail=None,
            code="internal_error",
        )


HTTP_STATUS_TITLES: dict[int, str] = {
    400: "Некорректный запрос",
    404: "Ресурс не найден",
    409: "Конфликт состояния",
    422: "Нарушено бизнес-правило",
    500: "Внутренняя ошибка сервера",
}
