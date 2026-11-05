"""Общие фикстуры тестов."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Клиент API поверх in-process приложения (без БД и Redis)."""
    return TestClient(create_app(), raise_server_exceptions=False)
