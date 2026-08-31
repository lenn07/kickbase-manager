"""Prometheus-Metriken für den Auto-Manager (Phase 7).

Zentrale Registry + typisierte Metrik-Objekte. Die Registry ist bewusst
modul-scoped statt der `prometheus_client.REGISTRY` — so kollidieren
Test-App-Instanzen (mehrere `create_app()` pro Testlauf) nicht mit
`Duplicated timeseries in CollectorRegistry`.
"""

from app.infrastructure.metrics.registry import (
    MetricsRegistry,
    get_metrics,
    reset_metrics,
)

__all__ = ["MetricsRegistry", "get_metrics", "reset_metrics"]
