"""Общие фикстуры тестов."""

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """Клиент API поверх in-process приложения (без БД и Redis)."""
    return TestClient(create_app(), raise_server_exceptions=False)
