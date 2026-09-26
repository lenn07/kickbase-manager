"""Domain-Modell der Auto-Manager-Entscheidungen (F-5).

`HOLD` ist eine gleichwertige Option (§ 7 Aggressivität): kein Kauf, kein
Verkauf, kein Angebot. Der Scheduler-Tick landet immer mit einer Entscheidung
im `trade_log`, damit Nachvollziehbarkeit gewahrt bleibt.

Verkaufs-Semantik ist zweigeteilt:
- `LIST_ON_MARKET` legt ein Transfermarkt-Listing zum Wunschpreis an; andere
  Manager können darauf bieten (24 h Laufzeit).
- `SELL` ist der Direktverkauf an Kickbase zum aktuellen Marktwert. Setzt
  voraus, dass der Spieler bereits gelistet ist — die Engine nutzt diese
  Aktion sowohl proaktiv (wenn Direktverkauf klüger ist als Warten) als auch
  als Fallback für Listings, die nach 24 h keinen Bieter gefunden haben.

`TradeIntent` klassifiziert die Motivation hinter einer Kauf-/Verkauf-Aktion
(Kader füllen, Wertsteigerung realisieren, Punkte sammeln, Schuldenabbau).
Wird im Dashboard als Badge angezeigt und beim SELL benutzt, um frühere
PROFIT-Käufe gezielt zu realisieren.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.lineup import Lineup


class TradeAction(StrEnum):
    BUY = "BUY"
    LIST_ON_MARKET = "LIST_ON_MARKET"
    SELL = "SELL"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"
    # Aufstellung schreiben. Die einzige Aktion, die unmittelbar Punkte bewegt:
    # jeder leere Startelf-Slot kostet 100 Punkte, ganz ohne Gegenleistung.
    SET_LINEUP = "SET_LINEUP"
    HOLD = "HOLD"


class TradeIntent(StrEnum):
    SQUAD_FILL = "SQUAD_FILL"
    PROFIT = "PROFIT"
    POINTS = "POINTS"
    SQUAD_TRIM = "SQUAD_TRIM"
    DEBT_RELIEF = "DEBT_RELIEF"
    # NONE = kein sinnvoller Motivations-Grund (v. a. für HOLD und DECLINE);
    # entspricht der Enum-Ausprägung im AI-Only-Modus-Prompt.
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class TradeDecision:
    action: TradeAction
    reason: str
    player_id: str | None = None
    player_name: str | None = None
    price: Decimal | None = None
    offer_id: str | None = None
    intent: TradeIntent | None = None
    # Nur bei `SET_LINEUP` gesetzt: Formation + Spieler-IDs in Slot-Reihenfolge.
    lineup: Lineup | None = None
    # Weitere Aktionen desselben Ticks, in Ausführungsreihenfolge (P2-16).
    #
    # Nur im **Deadline-Fenster** (< 2 h bis Anpfiff) gefüllt: dort muss der Bot
    # „verkaufen, aufstellen, nachkaufen" in einem Zug schaffen, weil kein
    # zweiter Tick mehr kommt. Sonst leer — eine Aktion pro Tick bleibt die
    # Regel, und jede zusätzliche Aktion ist eine, die niemand mehr korrigiert.
    #
    # Die Kette bricht beim ersten Fehler ab: eine Aktion, die auf einer
    # gescheiterten aufbaut (nachkaufen ohne den Verkauf davor), würde von
    # Kickbase ohnehin abgelehnt und den Rest der Kette mitreissen.
    follow_ups: tuple[TradeDecision, ...] = ()

    @classmethod
    def hold(cls, reason: str) -> TradeDecision:
        return cls(action=TradeAction.HOLD, reason=reason)

    @property
    def is_hold(self) -> bool:
        return self.action is TradeAction.HOLD

    @property
    def chain(self) -> tuple[TradeDecision, ...]:
        """Die ganze Aktionskette dieses Ticks, Hauptaktion zuerst.

        Aufrufer, die *alles* ausführen oder protokollieren wollen, iterieren
        hierüber statt `follow_ups` separat zu behandeln — so kann keine
        Aktion vergessen werden, wenn später eine dazukommt.
        """
        return (self, *self.follow_ups)
