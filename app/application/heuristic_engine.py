"""Heuristik-Kurator (ADR-5, Schicht 1) — ersetzt HoldOnlyDecisionEngine.

Der Algorithmus wählt pro Tick höchstens eine Aktion aus fünf Kategorien
(BUY, SELL, ACCEPT_OFFER, DECLINE_OFFER, HOLD). Jede Kategorie liefert eine
Utility ∈ [0, 1]; die höchste Utility gewinnt, sofern sie `min_action_score`
überschreitet — sonst HOLD.

Marktwert-Historie wird nur für vorgefilterte Kandidaten geladen (Top-N Markt +
gesamter Squad), damit die Extra-Requests klein bleiben und das Ban-Risiko
niedrig ist. Angebote auf eigene Spieler tauchen im Markt-Endpoint als
`MarketPlayer` mit `seller_id == manager_id` und gefüllter `offers`-Tupel auf.

`propose()` gibt die geordnete Kandidaten-Liste öffentlich zurück; der
LLM-Kurator (Phase 5) baut darauf auf, ohne die Heuristik nachbauen zu müssen.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from app.application.decision_engine import DecisionContext
from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.domain.kb_rules import (
    RuleAdjustment,
    action_threshold_scale,
    apply_delta,
    evaluate_accept_offer,
    evaluate_buy,
    evaluate_sell,
)
from app.domain.models import (
    MarketOffer,
    MarketPlayer,
    MarketValuePoint,
    Player,
    PlayerStatus,
    SquadPlayer,
)
from app.domain.scoring import (
    ScoreFeatures,
    ScoreWeights,
    compose_score,
    compute_features,
)
from app.domain.trade import TradeAction, TradeDecision

_log = logging.getLogger(__name__)

_ACCEPT_UTILITY_HALF_RANGE = 0.5
_DECLINE_UTILITY_HALF_RANGE = 0.5
_DEFAULT_MAX_MARKET_CANDIDATES = 10
_DEFAULT_HISTORY_DAYS = 7
_SQUAD_KEEP_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class HeuristicCandidate:
    """Ein von der Heuristik vorgeschlagener Zug — stabile ID + Utility + Decision.

    Die ID ist LLM-freundlich (`BUY:m1`, `SELL:s1`, `ACCEPT:o1`, `DECLINE:o1`)
    und dient dem Kurator als Auswahl-Handle. `summary` ist die kompakte
    Feature-Zusammenfassung, die 1:1 im Prompt landen kann.
    """

    id: str
    utility: float
    decision: TradeDecision
    summary: str


@dataclass(frozen=True, slots=True)
class _ScoredMarket:
    market: MarketPlayer
    score: float
    features: ScoreFeatures


@dataclass(frozen=True, slots=True)
class _ScoredSquad:
    squad: SquadPlayer
    score: float
    features: ScoreFeatures


class HeuristicDecisionEngine:
    def __init__(
        self,
        kickbase: KickbaseGateway,
        weights: ScoreWeights | None = None,
        *,
        max_market_candidates: int = _DEFAULT_MAX_MARKET_CANDIDATES,
        history_days: int = _DEFAULT_HISTORY_DAYS,
    ) -> None:
        self._kickbase = kickbase
        self._weights = weights or ScoreWeights()
        self._max_market_candidates = max_market_candidates
        self._history_days = history_days

    async def decide(self, context: DecisionContext) -> TradeDecision:
        candidates = await self.propose(context)
        if not candidates:
            return TradeDecision.hold(
                f"HOLD: keine Aktion verfügbar "
                f"(spendable={self._spendable_budget(context)}, "
                f"squad={len(context.squad.players)})."
            )

        scale = action_threshold_scale(
            now=context.now,
            next_matchday_start=context.next_matchday_start,
            interval_min=context.interval_min,
        )
        effective_threshold = context.min_action_score * scale

        best = candidates[0]
        if best.utility < effective_threshold:
            return TradeDecision.hold(
                f"HOLD: beste Utility {best.utility:.2f} < Schwelle "
                f"{effective_threshold:.2f} (Basis {context.min_action_score:.2f}, "
                f"Deadline-Skala {scale:.2f}, Kandidat: {best.decision.action})."
            )
        return best.decision

    async def propose(self, context: DecisionContext) -> tuple[HeuristicCandidate, ...]:
        """Liefert alle qualifizierten Kandidaten, absteigend nach Utility sortiert."""
        spendable = self._spendable_budget(context)
        manager_id = context.squad.manager_id
        squad_ids = {sp.player.id for sp in context.squad.players}

        buy_prefilter = self._prefilter_buy_market(
            context.market,
            blacklist=set(context.blacklist),
            squad_ids=squad_ids,
            spendable=spendable,
            manager_id=manager_id,
        )
        top_buy_prefilter = self._top_by_quick_score(
            buy_prefilter, limit=self._max_market_candidates
        )
        offer_market_entries = [
            mp for mp in context.market if mp.seller_id == manager_id and mp.offers
        ]

        history_targets: list[Player] = [sp.player for sp in context.squad.players]
        history_targets.extend(mp.player for mp in top_buy_prefilter)
        history_targets.extend(mp.player for mp in offer_market_entries)
        history_map = await self._load_histories(
            league_id=context.league_id, players=tuple(history_targets)
        )

        squad_scored = [
            self._score_squad(sp, history_map.get(sp.player.id, [])) for sp in context.squad.players
        ]
        squad_score_by_id = {ss.squad.player.id: ss for ss in squad_scored}
        buy_scored = [
            self._score_market(mp, history_map.get(mp.player.id, [])) for mp in top_buy_prefilter
        ]

        candidates: list[HeuristicCandidate] = []
        if spendable > 0:
            buy = self._best_buy(buy_scored)
            if buy is not None:
                candidates.append(self._apply_buy_rules(buy, context))
        sell = self._best_sell(squad_scored)
        if sell is not None:
            candidates.append(self._apply_sell_rules(sell, context))
        for candidate in self._collect_offer_candidates(offer_market_entries, squad_score_by_id):
            candidates.append(self._apply_offer_rules(candidate, context))
        candidates.sort(key=lambda c: c.utility, reverse=True)
        return tuple(candidates)

    # -- Guardrails & Prefilter ---------------------------------------

    @staticmethod
    def _spendable_budget(context: DecisionContext) -> int:
        by_pct = int(float(context.budget) * context.max_trade_pct)
        by_reserve = int(context.budget) - context.min_cash_reserve
        return max(0, min(by_pct, by_reserve))

    @staticmethod
    def _prefilter_buy_market(
        market: tuple[MarketPlayer, ...],
        *,
        blacklist: set[str],
        squad_ids: set[str],
        spendable: int,
        manager_id: str,
    ) -> list[MarketPlayer]:
        out: list[MarketPlayer] = []
        for mp in market:
            if mp.seller_id == manager_id:
                continue
            if mp.player.id in squad_ids or mp.player.id in blacklist:
                continue
            if mp.player.status is not PlayerStatus.FIT:
                continue
            price = int(mp.price)
            if price <= 0 or price > spendable:
                continue
            out.append(mp)
        return out

    def _top_by_quick_score(self, market: list[MarketPlayer], *, limit: int) -> list[MarketPlayer]:
        if len(market) <= limit:
            return market
        scored = [self._score_market(mp, history=[]) for mp in market]
        scored.sort(key=lambda s: s.score, reverse=True)
        return [s.market for s in scored[:limit]]

    # -- Scoring ------------------------------------------------------

    def _score_market(self, mp: MarketPlayer, history: list[MarketValuePoint]) -> _ScoredMarket:
        features = compute_features(mp.player, mp.price, history)
        return _ScoredMarket(
            market=mp, score=compose_score(features, self._weights), features=features
        )

    def _score_squad(self, sp: SquadPlayer, history: list[MarketValuePoint]) -> _ScoredSquad:
        features = compute_features(sp.player, sp.player.market_value, history)
        return _ScoredSquad(
            squad=sp, score=compose_score(features, self._weights), features=features
        )

    # -- Historie -----------------------------------------------------

    async def _load_histories(
        self, *, league_id: str, players: tuple[Player, ...]
    ) -> dict[str, list[MarketValuePoint]]:
        if not players:
            return {}
        unique_ids = {p.id for p in players}

        async def load(pid: str) -> tuple[str, list[MarketValuePoint]]:
            try:
                hist = await self._kickbase.get_market_value_history(
                    league_id, pid, days=self._history_days
                )
            except KickbaseError as exc:
                _log.info(
                    "Marktwert-Historie für %s fehlgeschlagen (%s) — Trend=neutral.", pid, exc
                )
                return pid, []
            return pid, list(hist)

        results = await asyncio.gather(*(load(pid) for pid in unique_ids))
        return dict(results)

    # -- Aktions-Kandidaten -------------------------------------------

    @staticmethod
    def _best_buy(scored: list[_ScoredMarket]) -> HeuristicCandidate | None:
        if not scored:
            return None
        top = max(scored, key=lambda s: s.score)
        decision = TradeDecision(
            action=TradeAction.BUY,
            reason=_score_reason("BUY", top.market.player, top.score, top.features),
            player_id=top.market.player.id,
            player_name=_full_name(top.market.player),
            price=top.market.price,
        )
        return HeuristicCandidate(
            id=f"BUY:{top.market.player.id}",
            utility=top.score,
            decision=decision,
            summary=decision.reason,
        )

    @staticmethod
    def _best_sell(scored: list[_ScoredSquad]) -> HeuristicCandidate | None:
        if not scored:
            return None
        worst = min(scored, key=lambda s: s.score)
        utility = 1.0 - worst.score
        decision = TradeDecision(
            action=TradeAction.SELL,
            reason=_score_reason("SELL", worst.squad.player, worst.score, worst.features),
            player_id=worst.squad.player.id,
            player_name=_full_name(worst.squad.player),
            price=worst.squad.player.market_value,
        )
        return HeuristicCandidate(
            id=f"SELL:{worst.squad.player.id}",
            utility=utility,
            decision=decision,
            summary=decision.reason,
        )

    # -- Kickbase-Regel-Modifikatoren ---------------------------------

    @staticmethod
    def _apply_buy_rules(
        candidate: HeuristicCandidate, context: DecisionContext
    ) -> HeuristicCandidate:
        assert candidate.decision.price is not None  # per Konstruktion im _best_buy
        adjustment = evaluate_buy(
            budget=context.budget,
            team_value=context.team_value,
            open_bids_total=context.open_bids_total,
            buy_price=Decimal(candidate.decision.price),
            squad_size=len(context.squad.players),
            now=context.now,
            next_matchday_start=context.next_matchday_start,
        )
        return _adjust_candidate(candidate, adjustment)

    @staticmethod
    def _apply_sell_rules(
        candidate: HeuristicCandidate, context: DecisionContext
    ) -> HeuristicCandidate:
        assert candidate.decision.price is not None
        adjustment = evaluate_sell(
            budget=context.budget,
            sell_price=Decimal(candidate.decision.price),
            squad_size=len(context.squad.players),
            now=context.now,
            next_matchday_start=context.next_matchday_start,
        )
        return _adjust_candidate(candidate, adjustment)

    @staticmethod
    def _apply_offer_rules(
        candidate: HeuristicCandidate, context: DecisionContext
    ) -> HeuristicCandidate:
        if candidate.decision.action is not TradeAction.ACCEPT_OFFER:
            # DECLINE ändert Squad/Konto nicht — keine Regel-Anpassung nötig.
            return candidate
        assert candidate.decision.price is not None
        adjustment = evaluate_accept_offer(
            budget=context.budget,
            offer_price=Decimal(candidate.decision.price),
            squad_size=len(context.squad.players),
            now=context.now,
            next_matchday_start=context.next_matchday_start,
        )
        return _adjust_candidate(candidate, adjustment)

    @staticmethod
    def _collect_offer_candidates(
        entries: list[MarketPlayer], squad_scores: dict[str, _ScoredSquad]
    ) -> list[HeuristicCandidate]:
        out: list[HeuristicCandidate] = []
        for mp in entries:
            scored = squad_scores.get(mp.player.id)
            mv = int(mp.player.market_value)
            if mv <= 0:
                continue
            best_offer = max(mp.offers, key=lambda o: o.price)
            worst_offer = min(mp.offers, key=lambda o: o.price)
            out.append(_accept_candidate(mp.player, best_offer, mv))
            if scored is not None and scored.score >= _SQUAD_KEEP_THRESHOLD:
                out.append(_decline_candidate(mp.player, worst_offer, mv, scored.score))
        return out


def _accept_candidate(player: Player, offer: MarketOffer, market_value: int) -> HeuristicCandidate:
    ratio = float(offer.price) / float(market_value)
    utility = _clip01((ratio - 1.0) / _ACCEPT_UTILITY_HALF_RANGE)
    decision = TradeDecision(
        action=TradeAction.ACCEPT_OFFER,
        reason=(
            f"ACCEPT Offer {offer.price} auf {player.last_name} "
            f"(MW={market_value}, Ratio={ratio:.2f})."
        ),
        player_id=player.id,
        player_name=_full_name(player),
        price=offer.price,
        offer_id=offer.id,
    )
    return HeuristicCandidate(
        id=f"ACCEPT:{offer.id}",
        utility=utility,
        decision=decision,
        summary=decision.reason,
    )


def _decline_candidate(
    player: Player, offer: MarketOffer, market_value: int, squad_score: float
) -> HeuristicCandidate:
    ratio = float(offer.price) / float(market_value)
    utility = _clip01((1.0 - ratio) / _DECLINE_UTILITY_HALF_RANGE)
    decision = TradeDecision(
        action=TradeAction.DECLINE_OFFER,
        reason=(
            f"DECLINE Offer {offer.price} auf {player.last_name} "
            f"(MW={market_value}, Score={squad_score:.2f})."
        ),
        player_id=player.id,
        player_name=_full_name(player),
        price=offer.price,
        offer_id=offer.id,
    )
    return HeuristicCandidate(
        id=f"DECLINE:{offer.id}",
        utility=utility,
        decision=decision,
        summary=decision.reason,
    )


def _adjust_candidate(
    candidate: HeuristicCandidate, adjustment: RuleAdjustment
) -> HeuristicCandidate:
    if adjustment.delta == 0.0 and not adjustment.reasons:
        return candidate
    new_utility = apply_delta(candidate.utility, adjustment.delta)
    rule_note = " | Regeln: " + "; ".join(adjustment.reasons) if adjustment.reasons else ""
    new_reason = f"{candidate.decision.reason}{rule_note}"
    new_decision = TradeDecision(
        action=candidate.decision.action,
        reason=new_reason,
        player_id=candidate.decision.player_id,
        player_name=candidate.decision.player_name,
        price=candidate.decision.price,
        offer_id=candidate.decision.offer_id,
    )
    return HeuristicCandidate(
        id=candidate.id,
        utility=new_utility,
        decision=new_decision,
        summary=f"{candidate.summary}{rule_note}",
    )


def _score_reason(action: str, player: Player, score: float, features: ScoreFeatures) -> str:
    return (
        f"{action} {player.last_name}: Score={score:.2f} "
        f"(Form={features.form:.2f}, Preis-Eff={features.price_efficiency:.2f}, "
        f"Trend={features.market_trend:.2f})."
    )


def _full_name(player: Player) -> str:
    return f"{player.first_name} {player.last_name}".strip()


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


__all__ = ["HeuristicCandidate", "HeuristicDecisionEngine", "ScoreWeights"]
