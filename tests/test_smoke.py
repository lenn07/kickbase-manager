"""Smoke-Tests — verifizieren, dass die App überhaupt startet."""

from app.main import app
from fastapi.testclient import TestClient


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_available() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Kickbase Auto-Manager"
