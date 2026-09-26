"""Ports (Interfaces) für externe Systeme — Implementierungen liegen in `infrastructure/`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.domain.lineup import Lineup
from app.domain.models import (
    CompetitionContext,
    Fixture,
    League,
    LeagueMe,
    MarketSnapshot,
    MarketValuePoint,
    Matchday,
    PlayerDetail,
    PlayerPerformance,
    Session,
    Squad,
    TeamStanding,
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
class MarketValueCache(Protocol):
    """Tages-Cache für Marktwert-Historien.

    Der Marktwert ändert sich **einmal am Tag**, um 22:00 Berlin (Plan §2.3),
    und Kickbase nennt den genauen Zeitpunkt als `mvud` im Market-Root. Eine
    Historie, die um 14:00 geholt wurde, ist um 16:00 also garantiert
    unverändert — der zweite Call liefert Byte für Byte dasselbe.

    Bis P1-7 hat der Bot sie trotzdem jeden Tick neu geholt: bei 120-min-Takt
    zwölfmal pro Tag je Spieler, für elf Datensätze, die sich nicht bewegt
    haben. Das ist der eigentliche Anteil an Defekt D8 und am Ban-Risiko aus
    §9 des Plans.

    `valid_until` ist deshalb kein TTL in Minuten, sondern der nächste
    Update-Zeitpunkt selbst. Kennt der Aufrufer ihn nicht, schreibt er nicht in
    den Cache — geraten wird hier nichts.
    """

    def get_many(
        self, league_id: str, player_ids: Sequence[str], *, now: datetime
    ) -> dict[str, list[MarketValuePoint]]:
        """Noch gültige Historien der genannten Spieler. Fehlende fehlen."""
        ...

    def put(
        self,
        league_id: str,
        player_id: str,
        points: Sequence[MarketValuePoint],
        *,
        valid_until: datetime,
    ) -> None: ...


@runtime_checkable
class PlayerPerformanceCache(Protocol):
    """Cache für Spieltags-Historien.

    Haltbarkeit ist hier `next_matchday_start`, nicht `mvud`: Spieltagspunkte
    stehen fest, sobald der Spieltag durch ist. Läuft gerade einer, liegt der
    nächste Start in der Zukunft — aber die Punkte des laufenden bewegen sich
    noch. Der Aufrufer löst das, indem er während eines laufenden Spieltags
    nicht schreibt; siehe `PlayerEnricher`.
    """

    def get_many(
        self, league_id: str, player_ids: Sequence[str], *, now: datetime
    ) -> dict[str, PlayerPerformance]: ...

    def put(
        self,
        league_id: str,
        player_id: str,
        performance: PlayerPerformance,
        *,
        valid_until: datetime,
    ) -> None: ...


@runtime_checkable
class CompetitionContextCache(Protocol):
    """Tages-Cache für Tabelle + Spielplan (P2-11).

    Die dritte Haltbarkeits-Frage im Projekt, und sie hat eine eigene Antwort:
    der Marktwert-Cache läuft bis `mvud`, der Spieltags-Cache bis zum nächsten
    Anpfiff — die Tabelle ändert sich ebenfalls nur durch gespielte Spiele, aber
    der Spielplan kann dazwischen verlegt werden. Gültigkeit ist deshalb
    „nächster Anpfiff, höchstens 24 Stunden" (siehe `TeamContextProvider`).

    Ein Eintrag je Wettbewerb, nicht je Liga: Tabelle und Spielplan der
    Bundesliga sind für alle Kickbase-Ligen dieselben.
    """

    def get(self, competition_id: str, *, now: datetime) -> CompetitionContext | None:
        """Noch gültiger Eintrag oder `None`."""
        ...

    def put(
        self, competition_id: str, context: CompetitionContext, *, valid_until: datetime
    ) -> None: ...


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

    async def get_market(self, league_id: str) -> MarketSnapshot:
        """Transfermarkt **inkl. Root-Feldern** (Mannschaftswert, Spieltagsstart, …).

        Gibt bewusst nicht nur die Listings zurück: `tv` (Mannschaftswert) und
        `dt` (nächster Spieltagsstart) stehen in derselben Response. Wer nur
        die Liste nimmt, wirft die 33 %-Regel-Basis weg — das war Defekt D1.
        """
        ...

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

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        """Einzelspieler-Detail — Quelle der ganzjährigen Startelf-Prognose `sl`.

        Kostet einen Request **pro Spieler**. Aufrufer müssen die Menge
        begrenzen; der `PlayerEnricher` holt sie nur für Kader + Shortlist und
        nur dann, wenn `prob` fehlt (Plan §9, Rate-Limit/Ban).
        """
        ...

    async def get_player_performance(self, league_id: str, player_id: str) -> PlayerPerformance:
        """Spieltags-Historie eines Spielers — Quelle der echten Form (P1-8).

        Die Response trägt **alle** Saisons seit 2016/17, rund 105 KB. Sie
        kostet einen Request pro Spieler und gehört deshalb hinter denselben
        Deckel wie `get_player_detail`: Kader + Shortlist, und nur, wenn kein
        gültiger Cache-Eintrag vorliegt.
        """
        ...

    async def get_lineup(self, league_id: str) -> Lineup:
        """Aktuelle Aufstellung: Formation + besetzte Slots in Slot-Reihenfolge."""
        ...

    async def set_lineup(self, league_id: str, lineup: Lineup) -> None:
        """Aufstellung schreiben. **Die einzige Aktion, die direkt Punkte bewegt.**

        Aufrufer müssen vorher `validate_lineup()` bestehen — der Executor tut
        das, unabhängig davon, was das Sprachmodell behauptet.
        """
        ...

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]: ...

    async def list_fixtures(self, competition_id: str = "1") -> list[Fixture]:
        """Alle Paarungen der Saison — Quelle des Restspielplans (P2-11).

        Trifft **denselben** Endpunkt wie `list_matchdays()`: `/matchdays`
        liefert alle 34 Spieltage mit `t1`/`t2`/`dt`/`st`. Zwei Methoden, weil
        die Aufrufer verschiedene Fragen stellen — der Tick will den nächsten
        Anpfiff (eine Zahl), der Spielplan-Kontext die Gegner (306 Zeilen) — und
        weil der Spielplan über den Tages-Cache läuft, der Anpfiff nicht.
        """
        ...

    async def get_competition_table(self, competition_id: str = "1") -> list[TeamStanding]:
        """Die Bundesliga-Tabelle, nach Platz sortiert (P2-11).

        Ligaweit, nicht spielerbezogen: 18 Zeilen beantworten die Frage nach der
        Gegnerstärke für jeden Spieler im Kader und auf dem Markt.
        """
        ...

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
