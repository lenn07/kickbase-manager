"""Wendet eine `TradeDecision` gegen Kickbase an — respektiert Dry-Run (F-7).

Trennt die Entscheidungs- von der Aktions-Verantwortung: der Executor darf
weder scoren noch loggen; er kennt nur `KickbaseGateway` und Dry-Run-Flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.domain.trade import TradeAction, TradeDecision


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    executed: bool
    reason: str
    error: str | None = None
    response_ref: str | None = None  # z. B. Offer-ID bei BUY


class TradeExecutor:
    def __init__(self, kickbase: KickbaseGateway, *, dry_run: bool) -> None:
        self._kickbase = kickbase
        self._dry_run = dry_run

    async def execute(self, league_id: str, decision: TradeDecision) -> ExecutionResult:
        if decision.is_hold:
            return ExecutionResult(executed=False, reason="HOLD — keine Aktion.")

        if self._dry_run:
            return ExecutionResult(
                executed=False, reason=f"Dry-Run — {decision.action} nicht abgesetzt."
            )

        try:
            ref = await self._dispatch(league_id, decision)
        except KickbaseError as exc:
            return ExecutionResult(executed=False, reason=str(exc), error=str(exc))

        return ExecutionResult(executed=True, reason="ok", response_ref=ref)

    async def _dispatch(self, league_id: str, decision: TradeDecision) -> str | None:
        if decision.action is TradeAction.BUY:
            if decision.player_id is None or decision.price is None:
                raise ValueError("BUY braucht player_id und price.")
            return await self._kickbase.place_bid(
                league_id, decision.player_id, Decimal(decision.price)
            )

        if decision.action is TradeAction.ACCEPT_OFFER:
            if decision.player_id is None or decision.offer_id is None:
                raise ValueError("ACCEPT_OFFER braucht player_id und offer_id.")
            await self._kickbase.accept_offer(league_id, decision.player_id, decision.offer_id)
            return None

        if decision.action is TradeAction.DECLINE_OFFER:
            if decision.player_id is None or decision.offer_id is None:
                raise ValueError("DECLINE_OFFER braucht player_id und offer_id.")
            await self._kickbase.decline_offer(league_id, decision.player_id, decision.offer_id)
            return None

        if decision.action is TradeAction.LIST_ON_MARKET:
            if decision.player_id is None or decision.price is None:
                raise ValueError("LIST_ON_MARKET braucht player_id und price.")
            return await self._kickbase.list_on_market(
                league_id, decision.player_id, Decimal(decision.price)
            )

        if decision.action is TradeAction.SELL:
            # Direktverkauf an Kickbase: der Preis ist der aktuelle Marktwert,
            # den die Engine für den Log/Notify-Pfad bereits mitschickt — die
            # API selbst braucht keinen Preis (DELETE /market/{pid}/sell).
            if decision.player_id is None:
                raise ValueError("SELL braucht player_id.")
            await self._kickbase.sell_to_kickbase(league_id, decision.player_id)
            return None

        raise ValueError(f"Unbekannte Trade-Aktion: {decision.action!r}")
