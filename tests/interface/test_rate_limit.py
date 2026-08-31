"""Tests für den Login-Rate-Limiter (Phase 7)."""

from __future__ import annotations

import time

import pytest
from app.interface.web.rate_limit import (
    LoginRateLimiter,
    enforce_login_rate_limit,
    login_limiter,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_global_limiter() -> None:
    login_limiter.reset()


def test_blockiert_nach_max_versuchen() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_s=60.0)
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(HTTPException) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_zaehler_pro_ip_getrennt() -> None:
    limiter = LoginRateLimiter(max_attempts=2, window_s=60.0)
    limiter.check("1.1.1.1")
    limiter.check("1.1.1.1")
    # 2.2.2.2 hat sein eigenes Kontingent
    limiter.check("2.2.2.2")
    limiter.check("2.2.2.2")
    with pytest.raises(HTTPException):
        limiter.check("1.1.1.1")


def test_fenster_lauft_ab(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = LoginRateLimiter(max_attempts=2, window_s=10.0)

    fake_time = [1000.0]

    def now() -> float:
        return fake_time[0]

    monkeypatch.setattr(time, "monotonic", now)

    limiter.check("9.9.9.9")
    limiter.check("9.9.9.9")
    with pytest.raises(HTTPException):
        limiter.check("9.9.9.9")

    fake_time[0] += 11.0
    # Nach Ablauf des Fensters muss wieder gehen.
    limiter.check("9.9.9.9")


def test_dependency_liest_forwarded_header() -> None:
    app = FastAPI()

    @app.get("/probe")
    async def probe(request: Request) -> dict[str, str]:
        enforce_login_rate_limit(request)
        return {"ok": "true"}

    login_limiter.reset()
    with TestClient(app) as client:
        headers = {"X-Forwarded-For": "10.0.0.7"}
        for _ in range(5):
            response = client.get("/probe", headers=headers)
            assert response.status_code == 200
        # 6. Versuch derselben IP → 429
        response = client.get("/probe", headers=headers)
        assert response.status_code == 429
        # Andere IP → wieder frei
        response = client.get("/probe", headers={"X-Forwarded-For": "10.0.0.8"})
        assert response.status_code == 200
