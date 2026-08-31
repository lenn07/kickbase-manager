"""Smoke-Tests — verifizieren, dass die App startet und Basisrouten liefert."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path)
    return TestClient(create_app(settings))


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Kickbase Auto-Manager"


def test_root_redirects_to_setup(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/kickbase"
