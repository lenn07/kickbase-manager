"""End-to-End-Tests für die Dashboard-Routen (Phase 6).

Setup wird via Wizard-Endpunkten durchgeführt (identisch zu `test_setup_router`),
damit die DB im gleichen Zustand landet wie im echten Betrieb. Kickbase- und
SMTP-Adapter werden gefaked, der LLM-Adapter akzeptiert den Test-Key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from app.application.setup_service import SetupService
from app.config import Settings
from app.interface.web.dependencies import (
    get_runtime_kickbase_client,
    get_session,
    get_setup_service,
)
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    app = create_app(settings)

    kickbase = FakeKickbase()
    llm = FakeLlm(valid_keys={"sk-ant-good"})
    smtp = FakeSmtp()
    vault = app.state.vault

    async def override_setup(
        session: Session = Depends(get_session),  # noqa: B008
    ) -> AsyncIterator[SetupService]:
        yield SetupService(
            session=session,
            vault=vault,
            kickbase=kickbase,
            llm=llm,
            smtp=smtp,
        )

    async def override_runtime_kickbase() -> AsyncIterator[FakeKickbase]:
        yield FakeKickbase()

    app.dependency_overrides[get_setup_service] = override_setup
    app.dependency_overrides[get_runtime_kickbase_client] = override_runtime_kickbase

    with TestClient(app) as tc:
        yield tc


def _complete_setup(client: TestClient) -> None:
    client.post("/setup/kickbase", data={"email": "user@example.com", "password": "secret"})
    client.post("/setup/leagues", data={"kb_league_id": "L1"})
    client.post("/setup/anthropic", data={"api_key": "sk-ant-good"})
    client.post(
        "/setup/smtp",
        data={
            "host": "smtp.example.com",
            "port": "465",
            "username": "u",
            "password": "p",
            "from_addr": "from@example.com",
            "to_addr": "to@example.com",
            "security": "tls",
        },
    )


def test_dashboard_redirects_when_setup_incomplete(client: TestClient) -> None:
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


def test_dashboard_renders_after_setup(client: TestClient) -> None:
    _complete_setup(client)
    r = client.get("/dashboard")
    assert r.status_code == 200
    body = r.text
    assert "Bundesliga Bros" in body
    assert "Live-Log" in body
    assert "Aktions-Historie" in body


def test_status_fragment_returns_html(client: TestClient) -> None:
    _complete_setup(client)
    r = client.get("/dashboard/status")
    assert r.status_code == 200
    assert "Budget" in r.text
    assert "Kader-Wert" in r.text


def test_status_fragment_conflicts_before_setup(client: TestClient) -> None:
    r = client.get("/dashboard/status")
    assert r.status_code == 409


def test_history_fragment_empty_after_setup(client: TestClient) -> None:
    _complete_setup(client)
    r = client.get("/dashboard/history")
    assert r.status_code == 200
    assert "Noch keine Ticks" in r.text


def test_history_fragment_conflicts_before_setup(client: TestClient) -> None:
    r = client.get("/dashboard/history")
    assert r.status_code == 409
