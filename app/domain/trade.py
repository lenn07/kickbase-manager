"""Domain-Modell der Auto-Manager-Entscheidungen (F-5).

`HOLD` ist eine gleichwertige Option (§ 7 Aggressivität): kein Kauf, kein
Verkauf, kein Angebot. Der Scheduler-Tick landet immer mit einer Entscheidung
im `trade_log`, damit Nachvollziehbarkeit gewahrt bleibt.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class TradeAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class TradeDecision:
    action: TradeAction
    reason: str
    player_id: str | None = None
    player_name: str | None = None
    price: Decimal | None = None
    offer_id: str | None = None

    @classmethod
    def hold(cls, reason: str) -> TradeDecision:
        return cls(action=TradeAction.HOLD, reason=reason)

    @property
    def is_hold(self) -> bool:
        return self.action is TradeAction.HOLD
