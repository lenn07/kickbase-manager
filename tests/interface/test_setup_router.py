"""End-to-End-Tests des Setup-Wizards via TestClient.

SetupService wird via `app.dependency_overrides` durch einen In-Memory-Fake ersetzt —
so brauchen wir keine echten Netzwerk-Calls gegen Kickbase, Anthropic oder SMTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from app.application.setup_service import SetupService
from app.config import Settings
from app.interface.web.dependencies import get_session, get_setup_service
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)

    kickbase = FakeKickbase()
    llm = FakeLlm(valid_keys={"sk-ant-good"})
    smtp = FakeSmtp()
    vault = app.state.vault

    async def override(
        session: Session = Depends(get_session),  # noqa: B008 — FastAPI-Signatur
    ) -> AsyncIterator[SetupService]:
        yield SetupService(
            session=session,
            vault=vault,
            kickbase=kickbase,
            llm=llm,
            smtp=smtp,
        )

    app.dependency_overrides[get_setup_service] = override
    with TestClient(app) as tc:
        yield tc


def test_root_redirects_to_kickbase_when_empty(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/kickbase"


def test_kickbase_form_renders(client: TestClient) -> None:
    response = client.get("/setup/kickbase")
    assert response.status_code == 200
    assert "Mit Kickbase anmelden" in response.text


def test_full_wizard_happy_path(client: TestClient) -> None:
    # Schritt 1: Login
    r = client.post(
        "/setup/kickbase",
        data={"email": "user@example.com", "password": "secret"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/leagues"

    # Schritt 2: Liga
    r = client.get("/setup/leagues")
    assert r.status_code == 200
    assert "Bundesliga Bros" in r.text
    r = client.post("/setup/leagues", data={"kb_league_id": "L1"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/anthropic"

    # Schritt 3: Anthropic
    r = client.post("/setup/anthropic", data={"api_key": "sk-ant-good"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/smtp"

    # Schritt 4: SMTP
    r = client.post(
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
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/done"

    r = client.get("/setup/done")
    assert r.status_code == 200
    assert "Bundesliga Bros" in r.text

    # /root leitet jetzt auf /setup/done statt zurück in den Wizard.
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/done"


def test_kickbase_form_shows_error_on_bad_password(client: TestClient) -> None:
    r = client.post(
        "/setup/kickbase",
        data={"email": "user@example.com", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Kickbase-Login fehlgeschlagen" in r.text


def test_htmx_redirect_uses_hx_header(client: TestClient) -> None:
    r = client.post(
        "/setup/kickbase",
        data={"email": "user@example.com", "password": "secret"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 204
    assert r.headers["HX-Redirect"] == "/setup/leagues"


def test_league_step_redirects_without_login(client: TestClient) -> None:
    r = client.get("/setup/leagues", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/kickbase"


def test_smtp_step_redirects_without_anthropic(client: TestClient) -> None:
    # Login + Liga vorbereiten
    client.post("/setup/kickbase", data={"email": "user@example.com", "password": "secret"})
    client.post("/setup/leagues", data={"kb_league_id": "L1"})
    r = client.get("/setup/smtp", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/anthropic"


def test_done_screen_redirects_when_incomplete(client: TestClient) -> None:
    r = client.get("/setup/done", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/kickbase"


def test_anthropic_error_stays_on_form(client: TestClient) -> None:
    client.post("/setup/kickbase", data={"email": "user@example.com", "password": "secret"})
    client.post("/setup/leagues", data={"kb_league_id": "L1"})
    r = client.post("/setup/anthropic", data={"api_key": "sk-ant-bad"}, follow_redirects=False)
    assert r.status_code == 200
    assert "invalid api key" in r.text.lower()
