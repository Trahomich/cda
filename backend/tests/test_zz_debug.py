"""Временный отладочный тест (удалить)."""

from app.main import create_app


def test_dbg_routes():
    app = create_app()
    routes = [
        r
        for r in app.router.routes
        if "sources" in getattr(getattr(r, "route", None), "path", "")
        or "sources" in getattr(r, "path", "")
    ]
    for r in routes:
        route = getattr(r, "route", r)
        print("PATH:", getattr(route, "path", None), getattr(route, "name", None))
        print("DEPS:", route.dependencies)
        print("APP_DEPS:", getattr(route, "app", None))
