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


class TradeAction(StrEnum):
    BUY = "BUY"
    LIST_ON_MARKET = "LIST_ON_MARKET"
    SELL = "SELL"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"
    HOLD = "HOLD"


class TradeIntent(StrEnum):
    SQUAD_FILL = "SQUAD_FILL"
    PROFIT = "PROFIT"
    POINTS = "POINTS"
    DEBT_RELIEF = "DEBT_RELIEF"


@dataclass(frozen=True, slots=True)
class TradeDecision:
    action: TradeAction
    reason: str
    player_id: str | None = None
    player_name: str | None = None
    price: Decimal | None = None
    offer_id: str | None = None
    intent: TradeIntent | None = None

    @classmethod
    def hold(cls, reason: str) -> TradeDecision:
        return cls(action=TradeAction.HOLD, reason=reason)

    @property
    def is_hold(self) -> bool:
        return self.action is TradeAction.HOLD
