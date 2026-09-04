"""Ports (Interfaces) für externe Systeme — Implementierungen liegen in `infrastructure/`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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

    async def list_on_market(self, league_id: str, player_id: str, price: Decimal) -> str:
        """Eigenen Spieler zum Wunschpreis auf den Transfermarkt setzen (Listing)."""
        ...

    async def sell_to_kickbase(self, league_id: str, player_id: str) -> None:
        """Spieler direkt an Kickbase (die „Bank") zum aktuellen Marktwert verkaufen.

        Setzt voraus, dass der Spieler bereits auf dem Markt liegt — Kickbase
        gibt automatisch ein Angebot in Marktwert-Höhe ab, das dieser Endpunkt
        annimmt.
        """
        ...

    async def remove_from_market(self, league_id: str, player_id: str) -> None:
        """Aktives eigenes Listing zurückziehen, ohne zu verkaufen."""
        ...

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> None: ...

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str) -> None: ...

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]: ...

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class ExternalDataGateway(Protocol):
    """Externe Team-Kontext-Signale (Form, Restspielplan) für das Scoring.

    Konkrete Implementierung (OpenLigaDB) liefert pro Kickbase-`team_id`
    ein Signal in [0, 1] — höher = besserer aktueller Kontext. Fehlende oder
    unbekannte Teams tauchen im Ergebnis nicht auf; der Aufrufer muss dann
    neutral (0.5) annehmen.
    """

    async def get_team_signals(self, team_ids: Iterable[str]) -> Mapping[str, float]: ...

    async def aclose(self) -> None: ...
