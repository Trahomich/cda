"""Временный отладочный тест (удалить)."""

from app.main import create_app
from fastapi.testclient import TestClient


def test_dbg_routes():
    app = create_app()
    c = TestClient(app)
    c.get("/api/v1/alerts")  # форсируем сборку маршрутов
    for r in app.router.routes:
        route = getattr(r, "route", r)
        path = getattr(route, "path", None) or getattr(r, "path", "")
        if "sources" in str(path) or "alerts" in str(path):
            deps = getattr(route, "dependencies", None)
            print("PATH:", path, "| name:", getattr(route, "name", None))
            print("DEPS:", deps)
