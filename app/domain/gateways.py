"""Ports (Interfaces) für externe Systeme — Implementierungen liegen in `infrastructure/`."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.domain.models import (
    League,
    LeagueMe,
    MarketPlayer,
    MarketValuePoint,
    Matchday,
    Session,
    Squad,
)


@runtime_checkable
class SessionStore(Protocol):
    """Persistente Cache-Schicht für Kickbase-Session + Login-Credentials.

    Der Client konsultiert den Store vor jedem authenticated Request, um
    Login-Roundtrips zu sparen (Ban-Schutz) und Sessions über Prozess-Restarts
    hinweg wiederzuverwenden.
    """

    async def load_session(self) -> Session | None: ...

    async def save_session(self, session: Session) -> None: ...

    async def load_credentials(self) -> tuple[str, str] | None: ...


@runtime_checkable
class KickbaseGateway(Protocol):
    """Alles, was die Anwendung von der Kickbase-API braucht.

    Die konkrete Implementierung kümmert sich um Auth, Token-Refresh, Rate-Limits
    und Fehler-Mapping. Aufrufer sieht nur Domain-Objekte und Domain-Exceptions.
    """

    async def login(self, email: str, password: str) -> Session: ...

    async def list_leagues(self) -> list[League]: ...

    async def get_league_me(self, league_id: str) -> LeagueMe: ...

    async def get_squad(self, league_id: str, manager_id: str) -> Squad: ...

    async def get_market(self, league_id: str) -> list[MarketPlayer]: ...

    async def place_bid(self, league_id: str, player_id: str, price: Decimal) -> str: ...

    async def sell_player(self, league_id: str, player_id: str, price: Decimal) -> str: ...

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> None: ...

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str) -> None: ...

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]: ...

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]: ...

    async def aclose(self) -> None: ...
