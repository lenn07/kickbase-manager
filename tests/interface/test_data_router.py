"""End-to-End-Tests der Datenverwaltung unter `/data`.

Setup läuft wie in den anderen Interface-Tests über die Wizard-Endpunkte,
damit die Datenbank im echten Betriebszustand steht. Der Kickbase-Adapter ist
gefaked — der Abgleich fragt Kader und Markt ab, und beide sollen im Test
steuerbar sein, nicht echt.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.application.setup_service import SetupService
from app.config import Settings
from app.infrastructure.persistence.models import TradeLogRow, UserRow
from app.infrastructure.persistence.repositories import MarketValueCacheRepository
from app.interface.web.dependencies import (
    get_runtime_kickbase_client,
    get_session,
    get_setup_service,
)
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    app = create_app(settings)

    kickbase = FakeKickbase()
    vault = app.state.vault

    async def override_setup(
        session: Session = Depends(get_session),  # noqa: B008
    ) -> AsyncIterator[SetupService]:
        yield SetupService(
            session=session,
            vault=vault,
            kickbase=kickbase,
            llm=FakeLlm(valid_keys={"sk-ant-good"}),
            smtp=FakeSmtp(),
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


def _add_buy(client: TestClient, player_id: str = "p1") -> int:
    """Ein ausgeführter Kauf im Log — der Rohstoff des Gedächtnisses."""
    engine = client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as db:
        user = db.exec(select(UserRow)).one()
        assert user.id is not None
        row = TradeLogRow(
            user_id=user.id,
            action="BUY",
            player_id=player_id,
            player_name="Testspieler",
            price=1_000_000,
            executed=True,
            context={"intent": "PROFIT"},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.id is not None
        return row.id


def _row(client: TestClient, row_id: int) -> TradeLogRow:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as db:
        return db.exec(select(TradeLogRow).where(TradeLogRow.id == row_id)).one()


# -- Seite -------------------------------------------------------------


def test_data_page_redirects_when_setup_incomplete(client: TestClient) -> None:
    r = client.get("/data", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


def test_data_page_renders_after_setup(client: TestClient) -> None:
    _complete_setup(client)
    r = client.get("/data")
    assert r.status_code == 200
    body = r.text
    assert "Mit Kickbase abgleichen" in body
    assert "Gedächtnis und Historie" in body
    assert "Zwischenspeicher" in body
    # Das Panel ist HTMX-Ziel und darf nur einmal vorkommen.
    assert body.count('id="data-panel"') == 1


def test_data_page_shows_the_masked_account(client: TestClient) -> None:
    _complete_setup(client)
    body = client.get("/data").text
    assert "us…@example.com" in body
    assert "user@example.com" not in body


# -- Aktionen ----------------------------------------------------------


def test_refresh_reports_an_empty_cache(client: TestClient) -> None:
    _complete_setup(client)
    r = client.post("/data/refresh")
    assert r.status_code == 200
    assert "Nichts zu verwerfen" in r.text


def test_reconcile_returns_the_panel_with_a_report(client: TestClient) -> None:
    _complete_setup(client)
    r = client.post("/data/reconcile")
    assert r.status_code == 200
    # Der Fake liefert leeren Kader und leeren Markt — beides wird berichtet.
    assert "Gesehen: 0 Spieler im Kader" in r.text
    assert 'id="data-panel"' in r.text


def test_reconcile_supersedes_a_buy_the_reality_contradicts(client: TestClient) -> None:
    _complete_setup(client)
    row_id = _add_buy(client)

    r = client.post("/data/reconcile")

    assert r.status_code == 200
    assert "als überholt markiert" in r.text
    assert _row(client, row_id).superseded_at is not None


def test_forget_marks_everything_without_deleting(client: TestClient) -> None:
    _complete_setup(client)
    row_id = _add_buy(client)

    r = client.post("/data/forget")

    assert r.status_code == 200
    assert "das Gedächtnis ist leer" in r.text
    row = _row(client, row_id)
    assert row.superseded_at is not None
    assert row.action == "BUY"  # Zeile lebt weiter


def test_forget_on_an_empty_log_says_so(client: TestClient) -> None:
    _complete_setup(client)
    r = client.post("/data/forget")
    assert r.status_code == 200
    assert "nichts mehr zu vergessen" in r.text


def test_purge_deletes_the_history(client: TestClient) -> None:
    _complete_setup(client)
    _add_buy(client)

    r = client.post("/data/purge")

    assert r.status_code == 200
    assert "1 Einträge gelöscht" in r.text
    engine = client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as db:
        assert list(db.exec(select(TradeLogRow))) == []


def test_superseded_rows_are_marked_in_the_dashboard_history(client: TestClient) -> None:
    _complete_setup(client)
    _add_buy(client)
    client.post("/data/forget")

    body = client.get("/dashboard/history").text

    assert "superseded" in body
    assert "überholt" in body


# -- Export ------------------------------------------------------------


def test_export_is_a_json_download(client: TestClient) -> None:
    _complete_setup(client)
    _add_buy(client)

    r = client.get("/data/export.json")

    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "kickbase-daten-" in r.headers["content-disposition"]
    payload = json.loads(r.text)
    assert payload["trade_log"]["total"] == 1
    assert payload["trade_log"]["entries"][0]["player_name"] == "Testspieler"
    assert payload["account"]["league_name"] == "Bundesliga Bros"


def test_export_carries_no_secrets(client: TestClient) -> None:
    _complete_setup(client)
    body = client.get("/data/export.json").text
    for forbidden in ("secret", "sk-ant-good", "user@example.com", "encrypted"):
        assert forbidden not in body


def test_refresh_clears_a_filled_cache(client: TestClient) -> None:
    _complete_setup(client)
    engine = client.app.state.engine  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    with Session(engine) as db:
        MarketValueCacheRepository(db).put("L1", "p1", [], valid_until=now + timedelta(hours=1))

    r = client.post("/data/refresh")

    assert "1 zwischengespeicherte Einträge verworfen" in r.text
    with Session(engine) as db:
        assert MarketValueCacheRepository(db).stats(now=now).rows == 0
