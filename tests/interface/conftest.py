"""Test-übergreifende Fixtures für Interface-Tests (Phase 7)."""

from __future__ import annotations

import pytest
from app.infrastructure.metrics import reset_metrics
from app.interface.web.rate_limit import login_limiter


@pytest.fixture(autouse=True)
def _reset_login_limiter() -> None:
    """Login-Limiter zwischen Tests zurücksetzen.

    Der Limiter ist prozess-weit (in-memory) und würde sonst über mehrere
    Tests hinweg akkumulieren — nach ein paar erfolgreichen POSTs bekämen
    Folge-Tests 429.
    """
    login_limiter.reset()


@pytest.fixture(autouse=True)
def _reset_prometheus_registry() -> None:
    """Prometheus-Registry frisch pro Test, damit Counter nicht überlaufen."""
    reset_metrics()
