"""Shared Fakes für SetupService-Tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.domain.exceptions import AuthError
from app.domain.models import (
    League,
    LeagueMe,
    MarketPlayer,
    MarketValuePoint,
    Matchday,
    Squad,
)
from app.domain.models import (
    Session as KbSession,
)
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.llm.anthropic_client import LlmVerificationError
from app.infrastructure.notifications.smtp_client import SmtpConfig, SmtpError

# Import registriert Tabellen bei SQLModel.metadata.
from app.infrastructure.persistence import models as _models  # noqa: F401
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


class FakeKickbase:
    def __init__(
        self,
        *,
        valid_credentials: tuple[str, str] = ("user@example.com", "secret"),
        leagues: list[League] | None = None,
    ) -> None:
        self._valid = valid_credentials
        self._leagues = leagues or [
            League(id="L1", name="Bundesliga Bros", creator_id="u1"),
            League(id="L2", name="Kanzlei-Kicker", creator_id="u1"),
        ]
        self.login_calls: list[tuple[str, str]] = []

    async def login(self, email: str, password: str) -> KbSession:
        self.login_calls.append((email, password))
        if (email, password) != self._valid:
            raise AuthError("bad creds")
        return KbSession(
            token="tkn-xyz",
            token_expires_at=datetime.now(UTC) + timedelta(days=7),
            user_id="u1",
            email=email,
        )

    async def list_leagues(self) -> list[League]:
        return list(self._leagues)

    async def get_league_me(self, league_id: str) -> LeagueMe:
        return LeagueMe(league_id=league_id, budget=Decimal(0))

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        return Squad(league_id=league_id, manager_id=manager_id, players=())

    async def get_market(self, league_id: str) -> list[MarketPlayer]:
        return []

    async def place_bid(self, league_id: str, player_id: str, price: Decimal) -> str:
        return "offer-x"

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        return None

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        return None

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]:
        return []

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        return []

    async def aclose(self) -> None:
        return None


class FakeLlm:
    def __init__(self, *, valid_keys: set[str] | None = None) -> None:
        self._valid = valid_keys or {"sk-ant-good"}
        self.calls: list[str] = []

    async def verify_key(self, api_key: str) -> None:
        self.calls.append(api_key)
        if api_key not in self._valid:
            raise LlmVerificationError("invalid api key")


class FakeSmtp:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.sent: list[tuple[SmtpConfig, str, str]] = []

    async def send(self, config: SmtpConfig, subject: str, body: str) -> None:
        if self._fail:
            raise SmtpError("smtp down")
        self.sent.append((config, subject, body))

    async def send_test_mail(self, config: SmtpConfig) -> None:
        await self.send(config, "test", "body")


@pytest.fixture
def engine() -> Iterator:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def db_session(engine) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    with Session(engine) as session:
        yield session


@pytest.fixture
def vault(tmp_path: Path) -> FernetVault:
    return FernetVault.load_or_create(data_dir=tmp_path)
