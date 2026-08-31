"""End-to-End-Tests für den Settings-Editor (§ 2.1 F-3, F-10).

Deckt insbesondere den Reschedule-Hook ab: eine Intervall-Änderung im
Formular muss `KickbaseScheduler.reschedule(new_interval)` triggern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from app.application.setup_service import SetupService
from app.config import Settings
from app.infrastructure.persistence.repositories import (
    SettingsRepository,
    UserRepository,
)
from app.infrastructure.scheduler.scheduler import SchedulerStatus
from app.interface.api.scheduler import get_scheduler
from app.interface.web.dependencies import (
    get_engine,
    get_runtime_kickbase_client,
    get_session,
    get_setup_service,
)
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp


class FakeScheduler:
    """Minimaler Stand-in, der Reschedule-Aufrufe protokolliert."""

    def __init__(self, *, interval_min: int = 120) -> None:
        self.interval_min = interval_min
        self.reschedule_calls: list[int] = []

    def reschedule(self, interval_min: int) -> None:
        if interval_min <= 0:
            raise ValueError("interval_min muss positiv sein.")
        self.interval_min = interval_min
        self.reschedule_calls.append(interval_min)

    def status(self) -> SchedulerStatus:
        return SchedulerStatus(
            running=True, paused=False, interval_min=self.interval_min, next_run=None
        )


@pytest.fixture
def scheduler() -> FakeScheduler:
    return FakeScheduler()


@pytest.fixture
def client(tmp_path: Path, scheduler: FakeScheduler) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    app = create_app(settings)

    kickbase = FakeKickbase()
    llm = FakeLlm(valid_keys={"sk-ant-good"})
    smtp = FakeSmtp()
    vault = app.state.vault

    async def override_setup(
        session: Session = Depends(get_session),  # noqa: B008
    ) -> AsyncIterator[SetupService]:
        yield SetupService(session=session, vault=vault, kickbase=kickbase, llm=llm, smtp=smtp)

    async def override_runtime_kickbase() -> AsyncIterator[FakeKickbase]:
        yield FakeKickbase()

    def override_scheduler() -> FakeScheduler:
        return scheduler

    app.dependency_overrides[get_setup_service] = override_setup
    app.dependency_overrides[get_runtime_kickbase_client] = override_runtime_kickbase
    app.dependency_overrides[get_scheduler] = override_scheduler

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


def _valid_form(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "interval_min": 120,
        "max_trade_pct": 0.25,
        "min_cash_reserve": 0,
        "min_action_score": 0.6,
        "digest_hour": 20,
        "blacklist": "",
    }
    base.update(overrides)
    return base


def test_settings_page_redirects_when_setup_incomplete(client: TestClient) -> None:
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


def test_settings_page_renders_defaults_after_setup(client: TestClient) -> None:
    _complete_setup(client)
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.text
    # Defaults aus SettingsRow:
    assert 'name="interval_min"' in body
    assert 'value="120"' in body
    assert 'name="max_trade_pct"' in body
    # Dry-Run ist per Default an → Checkbox checked.
    assert 'name="dry_run"' in body
    assert "checked" in body


def test_settings_submit_persists_and_reschedules_on_interval_change(
    client: TestClient, scheduler: FakeScheduler
) -> None:
    _complete_setup(client)

    r = client.post("/settings", data=_valid_form(interval_min=45, dry_run=""))
    assert r.status_code == 200
    assert "gespeichert" in r.text.lower()
    assert "45 min" in r.text  # Reschedule-Note im Fragment

    assert scheduler.reschedule_calls == [45]


def test_settings_submit_does_not_reschedule_when_interval_unchanged(
    client: TestClient, scheduler: FakeScheduler
) -> None:
    _complete_setup(client)

    # Erst Intervall auf 45 setzen (Reschedule #1),
    # dann Guardrail ändern ohne Intervall zu berühren.
    client.post("/settings", data=_valid_form(interval_min=45))
    scheduler.reschedule_calls.clear()

    r = client.post("/settings", data=_valid_form(interval_min=45, max_trade_pct=0.5))
    assert r.status_code == 200
    assert scheduler.reschedule_calls == []


def test_settings_submit_persists_all_guardrails(client: TestClient, tmp_path: Path) -> None:
    _complete_setup(client)

    r = client.post(
        "/settings",
        data=_valid_form(
            interval_min=180,
            dry_run="",  # dry_run off
            max_trade_pct=0.5,
            min_cash_reserve=1_000_000,
            min_action_score=1.25,
            digest_enabled="on",
            digest_hour=8,
            blacklist="player-1\nplayer-2\nplayer-1\n\n",  # duplicate + leerzeilen
        ),
    )
    assert r.status_code == 200

    # DB direkt gegenprüfen.
    engine = client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as db:
        user = UserRepository(db).get_singleton()
        assert user is not None and user.id is not None
        row = SettingsRepository(db).get_or_default(user.id)
        assert row.interval_min == 180
        assert row.dry_run is False
        assert row.max_trade_pct == 0.5
        assert row.min_cash_reserve == 1_000_000
        assert row.min_action_score == 1.25
        assert row.digest_enabled is True
        assert row.digest_hour == 8
        assert row.blacklist == ["player-1", "player-2"]


def test_settings_rejects_out_of_range_interval(
    client: TestClient, scheduler: FakeScheduler
) -> None:
    _complete_setup(client)

    r = client.post("/settings", data=_valid_form(interval_min=5))
    assert r.status_code == 200
    assert "zwischen" in r.text.lower()
    assert scheduler.reschedule_calls == []


def test_settings_rejects_bad_max_trade_pct(client: TestClient) -> None:
    _complete_setup(client)

    r = client.post("/settings", data=_valid_form(max_trade_pct=1.5))
    assert r.status_code == 200
    assert "max_trade_pct" in r.text


def test_settings_rejects_digest_hour_out_of_range(client: TestClient) -> None:
    _complete_setup(client)

    r = client.post("/settings", data=_valid_form(digest_hour=25))
    assert r.status_code == 200
    assert "digest_hour" in r.text


# Reference imports damit ruff die Fixtures nicht als ungenutzt markiert.
_ = get_engine
