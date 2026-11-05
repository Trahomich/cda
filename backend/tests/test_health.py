"""Smoke-тесты каркаса: /healthz и формат ошибок RFC 7807."""

from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unknown_route_is_rfc7807(client: TestClient) -> None:
    resp = client.get("/api/v1/no-such-endpoint")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    # CONTRACTS.md §8: type, title, status, detail + код приложения
    assert set(body) >= {"type", "title", "status"}
    assert body["status"] == 404


def test_app_error_is_rfc7807(client: TestClient) -> None:
    # эндпоинт для проверки обработчика AppError объявлен прямо в тесте
    from app.core.errors import AppError
    from app.main import create_app
    from fastapi import APIRouter

    app = create_app()
    router = APIRouter()

    @router.get("/_test/app-error")
    def raise_app_error() -> None:
        raise AppError(
            "нарушено бизнес-правило",
            code="rule_check_failed",
            status_code=422,
            title="Нарушено бизнес-правило",
        )

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/_test/app-error")
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "rule_check_failed"
    assert body["detail"] == "нарушено бизнес-правило"
    assert body["instance"] == "/_test/app-error"
