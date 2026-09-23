"""Domain-Entities — framework-frei, immutable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import IntEnum


class Position(IntEnum):
    GOALKEEPER = 1
    DEFENDER = 2
    MIDFIELDER = 3
    FORWARD = 4


class PlayerStatus(IntEnum):
    """Kickbase-`st`-Werte. Die Liste ist **nicht** nachweislich vollständig.

    Real beobachtet wurden bislang nur 0, 2 und 4 (Plan §8/F3); die API-Doku
    nennt zusätzlich 128. Die Frage ist durch Sammeln nicht abschließbar —
    deshalb gibt es `UNKNOWN`: ein `st`, das hier nicht steht, wird darauf
    abgebildet und **nicht** auf `FIT`. Ein nicht spielberechtigter Spieler,
    der als fit durchgeht, wird aufgestellt und kostet 100 Punkte (Defekt D6).

    `UNKNOWN` trägt bewusst einen negativen Wert: Kickbase vergibt nur
    nicht-negative, eine Kollision ist damit ausgeschlossen.
    """

    UNKNOWN = -1
    FIT = 0
    INJURED = 1
    UNKNOWN_2 = 2
    OUT_OF_SQUAD = 4
    REHAB = 8
    RED_CARD = 16
    YELLOW_RED_CARD = 32
    NOT_IN_TEAM = 64


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    token_expires_at: datetime
    user_id: str
    email: str


@dataclass(frozen=True, slots=True)
class League:
    id: str
    name: str
    creator_id: str
    budget: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Player:
    """Ein Bundesliga-Spieler, so wie Kickbase ihn liefert.

    `average_points`/`total_points` sind `None`, wenn Kickbase sie nicht
    mitschickt — und das kommt vor: 4 von 21 Marktspielern der Cassette tragen
    weder `ap` noch `p` (Spieler ohne Einsatz). Vor P0-3 stand dort 0.0, und
    0.0 ist hier nicht „keine Punkte", sondern „wir wissen es nicht". Genau
    diese Verwechslung war Defekt D1 in groß; §9 des Plans zieht daraus die
    Regel: fehlendes Feld ⇒ `None` + `missing_data`-Flag, nie Default-0.

    Ein **negativer** Wert ist dagegen echt (Platzverweis, Eigentor) und darf
    nicht als „fehlt" behandelt werden.
    """

    id: str
    first_name: str
    last_name: str
    team_id: str
    position: Position
    status: PlayerStatus
    market_value: Decimal
    average_points: float | None = None
    total_points: int | None = None


@dataclass(frozen=True, slots=True)
class SquadPlayer:
    player: Player
    # Kickbase v4 Squad-Response enthält keinen Kaufpreis mehr — bleibt 0.
    buy_price: Decimal = Decimal(0)
    # Startelf-Slot laut Kickbase (`lo`-Feld im Squad-Response). 0..10 =
    # aufgestellt (11 Slots), None/andere Werte = Bank/Reserve/unbekannt.
    lineup_order: int | None = None


@dataclass(frozen=True, slots=True)
class Squad:
    """Eigener Kader. **Kein** `team_value`/`budget` — bewusst.

    Beide Felder standen hier mit Default 0, obwohl `/squad` sie gar nicht
    liefert: der Mannschaftswert steht als `tv` im Market-Root (→
    `MarketSnapshot`), der Kontostand in `/leagues/{id}/me`. Die Default-0
    war die Ursache von Defekt D1 — sie sah aus wie ein Wert und war keiner,
    also lief die 33 %-Regel jeden Tick gegen eine 0-Basis. Wer hier wieder
    ein Default-Feld einzieht, baut denselben Defekt neu.
    """

    league_id: str
    manager_id: str
    players: tuple[SquadPlayer, ...]


@dataclass(frozen=True, slots=True)
class MarketOffer:
    id: str
    user_id: str
    user_name: str
    price: Decimal
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class MarketPlayer:
    """Ein Listing auf dem Transfermarkt.

    `expires_in_s` ist bewusst die **rohe** Restlaufzeit aus `exs` und keine
    absolute Zeit: Kickbase liefert Sekunden, und die Umrechnung braucht eine
    Uhr. Steht die Uhr in der DTO-Schicht (`datetime.now()`), ist der ganze
    USER-JSON nicht mehr reproduzierbar — der Payload-Snapshot aus P0-0.4 war
    deshalb bei jedem Lauf rot. Die Uhr gehört in den `DecisionContext`;
    `expires_at(now)` rechnet damit.

    Eigene Listings tragen **kein** `exs` (Plan §8/F5) — sie laufen nicht ab.
    Dort bleibt `expires_in_s` None, und das heißt „unbefristet", nicht
    „abgelaufen".
    """

    player: Player
    price: Decimal
    expires_in_s: int | None
    seller_id: str | None  # None → Kickbase-eigener Angebotspool
    # `ofc` = Anzahl abgegebener Gebote auf dieses Listing. Der billige
    # Indikator: er sagt *dass* geboten wurde, lange bevor klar ist, *wie* das
    # Gebots-Array im Payload heißt (Plan §8/F1). Auf eigenen Listings ist er
    # das Signal „warten statt Sofortverkauf", auf fremden ein Konkurrenzmaß.
    offer_count: int = 0
    # `prob` = Startelf-Wahrscheinlichkeit in 5 Stufen, `1` = sicherste Startelf
    # (Plan §8/F2). **Nur in der Spieltagswoche vorhanden** — am 23.09. in 0 von
    # 21 Items, am 31.08. in 22 von 22. Roh gehalten, damit die Umrechnung an
    # einer Stelle steht und sichtbar bleibt, wenn die Quelle fehlt.
    start_probability_raw: int | None = None
    # `isn` = neu auf dem Markt. `dt` im Item = Listing-Zeitpunkt (nicht zu
    # verwechseln mit `dt` im Root, das den Spieltagsstart meint).
    is_new: bool = False
    listed_at: datetime | None = None
    # Die Gebote selbst. Bleibt leer, bis der Feldname gegen ein echtes Gebot
    # verifiziert ist — ein geratenes Array wäre schlimmer als keins, weil
    # `ACCEPT_OFFER` dann mit einer erfundenen ID rausginge.
    offers: tuple[MarketOffer, ...] = ()

    @property
    def has_offers(self) -> bool:
        """Liegt mindestens ein Gebot vor?

        Stützt sich auf `ofc`, **nicht** auf `offers`: das Array ist bis zur
        Klärung von F1 immer leer, der Zähler ist echt.
        """
        return self.offer_count > 0 or bool(self.offers)

    def expires_at(self, now: datetime) -> datetime | None:
        if self.expires_in_s is None:
            return None
        return now + timedelta(seconds=self.expires_in_s)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Der Transfermarkt **plus** die Root-Felder derselben Response.

    Kickbase liefert unter `/v4/leagues/{l}/market` nicht nur die Listings,
    sondern auch den Mannschaftswert (`tv`), den nächsten Marktwert-Update-
    Zeitpunkt (`mvud`), den Start des nächsten Spieltags (`dt`), die
    Spieltagsnummer (`day`), die Kadergröße (`nps`) und die Saison (`sn`).
    Der Bot hat sie bisher weggeworfen und den Spieltagsstart stattdessen
    über einen zweiten Call (`list_matchdays`) geholt.

    `mv_update_at` ist der **nächste** Update-Zeitpunkt (Plan §8/F4, zweimal
    belegt), nicht der letzte — die Timing-Logik in P1-10 hängt daran.
    """

    players: tuple[MarketPlayer, ...]
    team_value: Decimal
    mv_update_at: datetime | None = None
    next_matchday_start: datetime | None = None
    matchday: int = 0
    squad_size: int = 0
    season: str = ""


@dataclass(frozen=True, slots=True)
class PlayerDetail:
    """Einzelspieler-Sicht aus `GET /v4/leagues/{l}/players/{p}`.

    Gehalten wird nur, was P0-3 braucht: die Startelf-Prognose `sl`. Sie ist
    ein **bool**, nicht die 5-stufige `prob`-Skala, dafür aber ganzjährig
    verfügbar — `prob` fehlt außerhalb der Spieltagswoche komplett (Plan
    §8/F2). Die Quelle (`plpt`, aktuell „Ligainsider") wandert mit ins
    USER-JSON: eine Prognose ohne Herkunft kann das Modell nicht gewichten.

    Kostet einen HTTP-Call pro Spieler — deshalb holt der Enricher sie nur für
    eine begrenzte Auswahl und nur, wenn `prob` fehlt.
    """

    player_id: str
    is_predicted_starter: bool | None = None
    prediction_source: str = ""


@dataclass(frozen=True, slots=True)
class Matchday:
    number: int
    starts_at: datetime
    ends_at: datetime
    is_current: bool


@dataclass(frozen=True, slots=True)
class MarketValuePoint:
    day: datetime
    value: Decimal


@dataclass(frozen=True, slots=True)
class LeagueMe:
    """Meine Sicht auf eine Liga: Budget, Team-Wert, Metadaten."""

    league_id: str
    budget: Decimal
    unread_notifications: int = 0
    is_admin: bool = False
