"""API-Tests für /api/scheduler/*.

Der Scheduler wird via TestClient-Lifespan hochgefahren, die Kickbase-,
LLM- und SMTP-Abhängigkeiten werden gepatcht, damit kein echter Netzwerk-Call
im Tick erfolgt.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from app.application.decision_engine import HoldOnlyDecisionEngine
from app.application.run_tick_uc import RunTickUseCase
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeSmtp


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def fake_tick_wire(engine: Engine, vault):  # type: ignore[no-untyped-def]
        with Session(engine) as db:
            uc = RunTickUseCase(
                session=db,
                vault=vault,
                kickbase=FakeKickbase(),
                engine=HoldOnlyDecisionEngine(),
                smtp=FakeSmtp(),
            )
            return await uc.run()

    monkeypatch.setattr("app.main._run_tick", fake_tick_wire)

    settings = Settings(data_dir=tmp_path, scheduler_enabled=True, default_interval_min=60)
    with TestClient(create_app(settings)) as tc:
        yield tc


def test_status_reports_running_scheduler(client: TestClient) -> None:
    r = client.get("/api/scheduler/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["running"] is True
    assert body["paused"] is False
    assert body["interval_min"] == 60


def test_pause_and_resume_flip_flag(client: TestClient) -> None:
    r = client.post("/api/scheduler/pause")
    assert r.status_code == 200
    assert r.json()["paused"] is True

    r = client.post("/api/scheduler/resume")
    assert r.status_code == 200
    assert r.json()["paused"] is False


def test_trigger_returns_skipped_when_setup_incomplete(client: TestClient) -> None:
    r = client.post("/api/scheduler/trigger")
    assert r.status_code == 200
    body = r.json()
    assert body["executed"] is False
    assert body["skipped_reason"] == "setup-incomplete"


def test_status_when_scheduler_disabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    with TestClient(create_app(settings)) as tc:
        r = tc.get("/api/scheduler/status")
        assert r.status_code == 200
        assert r.json() == {
            "enabled": False,
            "running": False,
            "paused": False,
            "interval_min": None,
            "next_run": None,
        }


def test_trigger_when_scheduler_disabled(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    with TestClient(create_app(settings)) as tc:
        r = tc.post("/api/scheduler/trigger")
        assert r.status_code == 503
