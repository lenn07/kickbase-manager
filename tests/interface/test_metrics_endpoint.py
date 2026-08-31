"""End-to-end-Tests für /metrics und die HTTP-Middleware (Phase 7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings
from app.infrastructure.metrics import reset_metrics
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    reset_metrics()
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client


def test_metrics_endpoint_liefert_prometheus_format(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # Kern-Metriken müssen registriert sein (auch mit Zähler 0 sichtbar).
    assert "kb_ticks_total" in body
    assert "kb_http_requests_total" in body
    assert "kb_scheduler_running" in body


def test_http_middleware_zaehlt_request(client: TestClient) -> None:
    # Health/metrics werden ignoriert; Setup-Root liefert einen 303-Redirect.
    client.get("/", follow_redirects=False)
    metrics = client.get("/metrics").text
    assert 'kb_http_requests_total{method="GET",route="/",status="303"} 1.0' in metrics


def test_metrics_scrape_nicht_in_zaehlern_enthalten(client: TestClient) -> None:
    client.get("/metrics")
    body = client.get("/metrics").text
    # /metrics selbst darf nicht als Route auftauchen.
    assert 'route="/metrics"' not in body


def test_health_endpoint_wird_nicht_gezaehlt(client: TestClient) -> None:
    client.get("/health")
    body = client.get("/metrics").text
    assert 'route="/health"' not in body
