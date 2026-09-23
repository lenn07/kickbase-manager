"""Wendet eine `TradeDecision` gegen Kickbase an — respektiert Dry-Run (F-7).

Trennt die Entscheidungs- von der Aktions-Verantwortung: der Executor darf
weder scoren noch loggen; er kennt nur `KickbaseGateway` und Dry-Run-Flag.

**Ausnahme: `SET_LINEUP`.** Diese eine Aktion validiert der Executor selbst,
bevor er sie absetzt — nicht, weil er mitentscheiden will, sondern weil eine
ungültige Aufstellung Punkte kostet, die kein späterer Tick zurückholt. Die
Prüfung gehört hierher und nicht in den Prompt: ein Prompt kann man
missverstehen, eine Vorbedingung nicht.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.domain.lineup import validate_lineup
from app.domain.models import SquadPlayer
from app.domain.trade import TradeAction, TradeDecision


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    executed: bool
    reason: str
    error: str | None = None
    response_ref: str | None = None  # z. B. Offer-ID bei BUY


class TradeExecutor:
    def __init__(
        self,
        kickbase: KickbaseGateway,
        *,
        dry_run: bool,
        squad: Sequence[SquadPlayer] = (),
        lineup_writes_enabled: bool = False,
    ) -> None:
        self._kickbase = kickbase
        self._dry_run = dry_run
        # Für die Aufstellungs-Vorvalidierung: ohne den Kader lässt sich nicht
        # prüfen, ob die genannten IDs überhaupt eigene Spieler sind.
        self._squad = tuple(squad)
        self._lineup_writes_enabled = lineup_writes_enabled

    async def execute(self, league_id: str, decision: TradeDecision) -> ExecutionResult:
        if decision.is_hold:
            return ExecutionResult(executed=False, reason="HOLD — keine Aktion.")

        if decision.action is TradeAction.SET_LINEUP:
            blocked = self._lineup_precheck(decision)
            if blocked is not None:
                return blocked

        if self._dry_run:
            return ExecutionResult(
                executed=False, reason=f"Dry-Run — {decision.action} nicht abgesetzt."
            )

        try:
            ref = await self._dispatch(league_id, decision)
        except KickbaseError as exc:
            return ExecutionResult(executed=False, reason=str(exc), error=str(exc))

        return ExecutionResult(executed=True, reason="ok", response_ref=ref)

    def _lineup_precheck(self, decision: TradeDecision) -> ExecutionResult | None:
        """Hält ungültige Aufstellungen zurück. None = darf raus.

        Läuft **vor** der Dry-Run-Abzweigung: eine ungültige Aufstellung soll
        auch im Shadow-Lauf auffallen, sonst wird der Fehler erst sichtbar, wenn
        er echtes Geld und echte Punkte kostet.
        """
        if not self._lineup_writes_enabled:
            return ExecutionResult(
                executed=False,
                reason=(
                    "Aufstellungs-Writes sind deaktiviert (KB_LINEUP_WRITES_ENABLED). "
                    "Vorgemerkt, nicht abgesetzt."
                ),
            )

        lineup = decision.lineup
        if lineup is None:
            return ExecutionResult(
                executed=False,
                reason="SET_LINEUP ohne Aufstellung.",
                error="SET_LINEUP ohne Aufstellung.",
            )

        errors = validate_lineup(lineup.player_ids, lineup.formation, self._squad)
        if errors:
            detail = " | ".join(errors)
            return ExecutionResult(
                executed=False,
                reason=f"Aufstellung abgelehnt: {detail}",
                error=detail,
            )
        return None

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

        if decision.action is TradeAction.SET_LINEUP:
            # `_lineup_precheck` hat bereits validiert — hier nur noch absetzen.
            assert decision.lineup is not None
            await self._kickbase.set_lineup(league_id, decision.lineup)
            return decision.lineup.formation

        raise ValueError(f"Unbekannte Trade-Aktion: {decision.action!r}")
