"""Tests für die Prometheus-Registry (Phase 7)."""

from __future__ import annotations

from app.infrastructure.metrics import MetricsRegistry
from prometheus_client import generate_latest


def test_record_tick_zaehlt_labels_getrennt() -> None:
    metrics = MetricsRegistry()
    metrics.record_tick("executed")
    metrics.record_tick("executed")
    metrics.record_tick("hold")

    payload = generate_latest(metrics.registry).decode()
    assert 'kb_ticks_total{outcome="executed"} 2.0' in payload
    assert 'kb_ticks_total{outcome="hold"} 1.0' in payload


def test_observe_http_erzeugt_counter_und_histogram() -> None:
    metrics = MetricsRegistry()
    metrics.observe_http(method="GET", route="/dashboard", status=200, duration_s=0.042)

    payload = generate_latest(metrics.registry).decode()
    assert 'kb_http_requests_total{method="GET",route="/dashboard",status="200"} 1.0' in payload
    assert "kb_http_request_duration_seconds_bucket" in payload
    assert 'method="GET"' in payload


def test_gauge_kann_scheduler_state_toggeln() -> None:
    metrics = MetricsRegistry()
    metrics.scheduler_running.set(1)
    metrics.scheduler_paused.set(0)

    payload = generate_latest(metrics.registry).decode()
    assert "kb_scheduler_running 1.0" in payload
    assert "kb_scheduler_paused 0.0" in payload
