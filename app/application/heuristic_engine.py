"""Heuristik-Kurator (ADR-5, Schicht 1) — ersetzt HoldOnlyDecisionEngine.

Der Algorithmus wählt pro Tick höchstens eine Aktion aus sechs Kategorien
(BUY, LIST_ON_MARKET, SELL, ACCEPT_OFFER, DECLINE_OFFER, HOLD). Jede Kategorie
liefert eine Utility ∈ [0, 1]; die höchste Utility gewinnt, sofern sie
`min_action_score` überschreitet — sonst HOLD.

Verkauf ist zweistufig modelliert: LIST_ON_MARKET (Wunschpreis = MW * 1.10,
wartet 24 h auf Bieter) und SELL (Direktverkauf an Kickbase zum Marktwert).
Pro Squad-Spieler entstehen beide Kandidaten; der mit der höheren Utility
gewinnt. Ein bereits gelistetes Listing wird nicht erneut vorgeschlagen,
außer der Stale-Fallback greift: läuft das Listing in < 2 h ab oder ist es
seit ≥ 24 h ohne Angebot offen, erzwingt die Engine einen SELL mit Priorität.

Marktwert-Historie wird nur für vorgefilterte Kandidaten geladen (Top-N Markt +
gesamter Squad), damit die Extra-Requests klein bleiben und das Ban-Risiko
niedrig ist. Angebote auf eigene Spieler tauchen im Markt-Endpoint als
`MarketPlayer` mit `seller_id == manager_id` und gefüllter `offers`-Tupel auf.

`propose()` gibt die geordnete Kandidaten-Liste öffentlich zurück; der
LLM-Kurator (Phase 5) baut darauf auf, ohne die Heuristik nachbauen zu müssen.

Intent-Ableitung (SQUAD_FILL / PROFIT / POINTS / DEBT_RELIEF) markiert jeden
BUY/SELL mit dem dominanten Kauf- bzw. Verkaufsgrund. Der Bot bietet für
wichtige Kandidaten bis zu +15 % über Marktwert (Overbid), damit ein starker
Spieler nicht an einen anderen Manager verloren geht. Frühere PROFIT-Käufe
werden im SELL-Scoring bevorzugt realisiert, sobald der Marktwert deutlich
über dem Kaufpreis liegt.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.application.decision_engine import BuyRecord, DecisionContext, ListingRecord
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
from app.domain.trade import TradeAction, TradeDecision, TradeIntent

_log = logging.getLogger(__name__)

_ACCEPT_UTILITY_HALF_RANGE = 0.5
_DECLINE_UTILITY_HALF_RANGE = 0.5
_DEFAULT_MAX_MARKET_CANDIDATES = 10
_DEFAULT_HISTORY_DAYS = 7
_SQUAD_KEEP_THRESHOLD = 0.5

# Squad-Fill: unterhalb dieser Kader-Größe steigt der BUY-Bonus linear.
_SQUAD_TARGET_SIZE = 15
_SQUAD_FILL_TRIGGER = 13
_SQUAD_FILL_MAX_BONUS = 0.15

# Overbid: max. 15 % über Marktpreis; skaliert linear ab einer Basis-Utility.
_OVERBID_MAX_PCT = 0.15
_OVERBID_UTILITY_FLOOR = 0.6

# Intent-Schwellen (leiten den Grund aus den Score-Features ab).
_PROFIT_TREND_THRESHOLD = 0.68  # market_trend > .68 → deutlicher Uptrend
_POINTS_FORM_THRESHOLD = 0.55  # form > .55 → Spieler bringt aktuell Punkte

# PROFIT-Exit-Bonus, wenn früherer Kauf jetzt deutlich im Plus steht.
_PROFIT_EXIT_MIN_GAIN = 0.10
_PROFIT_EXIT_MAX_GAIN = 0.30
_PROFIT_EXIT_MAX_BONUS = 0.35

# Listing vs. Direktverkauf: +10 % Wunschpreis-Aufschlag, ~50 % Verkaufs-
# wahrscheinlichkeit innerhalb der 24 h Listing-Dauer (Erfahrungswert für
# Preise nahe Marktwert), abzüglich kleinem Cash-Delay-Malus für die
# Warte­zeit gegenüber sofortigem Direktverkauf.
_LISTING_UPLIFT_PCT = 0.10
_LISTING_SELL_PROBABILITY = 0.5
_LISTING_CASH_DELAY_MALUS = 0.03

# Stale-Fallback-Schwellen: Listing zählt als "abgelaufen", wenn es entweder
# in <2 h ausläuft oder seit >=24 h ohne Angebot offen ist.
_STALE_LISTING_AGE = timedelta(hours=24)
_STALE_LISTING_DEADLINE_WINDOW = timedelta(hours=2)
_STALE_FALLBACK_UTILITY = 0.99


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
        squad_size = len(context.squad.players)

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
        listed_ids = set(context.own_listings.keys())

        candidates: list[HeuristicCandidate] = []
        if spendable > 0:
            buy = self._best_buy(buy_scored, spendable=spendable, squad_size=squad_size)
            if buy is not None:
                candidates.append(self._apply_buy_rules(buy, context))
        # Pro Squad-Spieler beide Verkaufs-Varianten erzeugen, die Kickbase-
        # Regeln (Debt-Relief, Startelf, Deadline) auf JEDE anwenden — und
        # erst danach pro Spieler die stärkere Variante behalten. Debt-Relief
        # ist z. B. für SELL voll wirksam, für LIST halbiert; die Reihenfolge
        # sicherzustellen ist wichtig, damit die Rule-Adjustments die Wahl
        # tatsächlich mitentscheiden.
        raw_sell_pairs = self._sell_candidate_pairs(
            squad_scored,
            buy_history=context.buy_history,
            listed_ids=listed_ids,
        )
        for list_cand, sell_cand in raw_sell_pairs:
            adjusted_list = self._apply_sell_rules(list_cand, context)
            adjusted_sell = self._apply_sell_rules(sell_cand, context)
            best = (
                adjusted_list if adjusted_list.utility >= adjusted_sell.utility else adjusted_sell
            )
            candidates.append(best)
        stale = self._stale_listing_candidates(context)
        candidates.extend(stale)
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

    def _best_buy(
        self,
        scored: list[_ScoredMarket],
        *,
        spendable: int,
        squad_size: int,
    ) -> HeuristicCandidate | None:
        if not scored:
            return None
        top = max(scored, key=lambda s: s.score)
        intent = _derive_buy_intent(top.features, squad_size=squad_size)
        fill_bonus = _squad_fill_bonus(squad_size)
        base_utility = _clip01(top.score + fill_bonus)
        bid_price = _compute_overbid_price(
            market_price=top.market.price, utility=base_utility, spendable=spendable
        )
        reason = _buy_reason(top.market.player, top.score, top.features, bid_price, intent)
        if fill_bonus > 0:
            reason += f" | Squad-Fill-Bonus +{fill_bonus:.2f}."
        if bid_price > top.market.price:
            uplift_pct = (float(bid_price) / float(top.market.price) - 1.0) * 100.0
            reason += f" | Overbid +{uplift_pct:.1f} %."
        decision = TradeDecision(
            action=TradeAction.BUY,
            reason=reason,
            player_id=top.market.player.id,
            player_name=_full_name(top.market.player),
            price=bid_price,
            intent=intent,
        )
        return HeuristicCandidate(
            id=f"BUY:{top.market.player.id}",
            utility=base_utility,
            decision=decision,
            summary=decision.reason,
        )

    @staticmethod
    def _sell_candidate_pairs(
        scored: list[_ScoredSquad],
        *,
        buy_history: Mapping[str, BuyRecord],
        listed_ids: set[str],
    ) -> list[tuple[HeuristicCandidate, HeuristicCandidate]]:
        """Erzeugt pro Squad-Spieler das Paar (LIST_ON_MARKET, SELL).

        Bereits gelistete Spieler werden übersprungen — für sie ist entweder
        der Stale-Fallback zuständig oder ein bereits laufendes Listing.
        """
        if not scored:
            return []
        history_map: dict[str, BuyRecord] = dict(buy_history) if buy_history else {}

        out: list[tuple[HeuristicCandidate, HeuristicCandidate]] = []
        for entry in scored:
            player = entry.squad.player
            if player.id in listed_ids:
                continue
            base_utility = 1.0 - entry.score
            intent = TradeIntent.POINTS  # Default: schwacher Kader-Spieler weg
            profit_bonus = 0.0
            profit_note = ""
            record = history_map.get(player.id)
            if record is not None and record.intent is TradeIntent.PROFIT:
                gain_ratio = _relative_gain(current=player.market_value, buy_price=record.buy_price)
                if gain_ratio >= _PROFIT_EXIT_MIN_GAIN:
                    profit_bonus = _profit_exit_bonus(gain_ratio)
                    intent = TradeIntent.PROFIT
                    profit_note = (
                        f" | PROFIT-Exit: +{gain_ratio * 100:.1f} % über Kaufpreis "
                        f"({int(record.buy_price):,}), Bonus +{profit_bonus:.2f}."
                    )
            sell_utility = _clip01(base_utility + profit_bonus)
            list_utility = _clip01(
                sell_utility + _listing_expected_bonus() - _LISTING_CASH_DELAY_MALUS
            )
            list_price = _listing_wish_price(player.market_value)

            reason_core = _score_reason("SELL", player, entry.score, entry.features) + profit_note
            sell_decision = TradeDecision(
                action=TradeAction.SELL,
                reason=(
                    f"SELL-DIREKT {player.last_name} zum Marktwert "
                    f"({int(player.market_value):,}). {reason_core}"
                ),
                player_id=player.id,
                player_name=_full_name(player),
                price=player.market_value,
                intent=intent,
            )
            list_decision = TradeDecision(
                action=TradeAction.LIST_ON_MARKET,
                reason=(
                    f"LIST {player.last_name} für {int(list_price):,} "
                    f"(MW {int(player.market_value):,} +{int(_LISTING_UPLIFT_PCT * 100)} %). "
                    f"{reason_core}"
                ),
                player_id=player.id,
                player_name=_full_name(player),
                price=list_price,
                intent=intent,
            )
            list_cand = HeuristicCandidate(
                id=f"LIST:{player.id}",
                utility=list_utility,
                decision=list_decision,
                summary=list_decision.reason,
            )
            sell_cand = HeuristicCandidate(
                id=f"SELL:{player.id}",
                utility=sell_utility,
                decision=sell_decision,
                summary=sell_decision.reason,
            )
            out.append((list_cand, sell_cand))
        return out

    def _stale_listing_candidates(self, context: DecisionContext) -> list[HeuristicCandidate]:
        """SELL mit Priorität für eigene Listings, die zu lange offen liegen.

        Löst die Vorgabe „nach bestimmter Zeit direkt an Kickbase verkaufen,
        wenn kein anderer Manager gekauft hat". Utility ist bewusst nahe 1.0,
        damit der Fallback im Zweifel gewinnt — aber unterhalb einer echten
        ACCEPT-Reaktion auf ein starkes Manager-Angebot, das der gleiche
        Market-Tick liefert.
        """
        if not context.own_listings or context.now is None:
            return []
        out: list[HeuristicCandidate] = []
        for player_id, record in context.own_listings.items():
            if record.has_offers:
                # Sobald ein Manager-Angebot vorliegt, entscheidet der
                # ACCEPT/DECLINE-Pfad — Stale-Fallback wäre voreilig.
                continue
            reason = _stale_reason(context.now, record)
            if reason is None:
                continue
            player_name = _lookup_squad_name(context, player_id) or player_id
            decision = TradeDecision(
                action=TradeAction.SELL,
                reason=(
                    f"Stale-Listing: {player_name} seit {reason} ohne Bieter → "
                    f"Direktverkauf an Kickbase zum Marktwert."
                ),
                player_id=player_id,
                player_name=player_name,
                price=record.listing_price,
                intent=TradeIntent.PROFIT,
            )
            out.append(
                HeuristicCandidate(
                    id=f"SELL:{player_id}",
                    utility=_STALE_FALLBACK_UTILITY,
                    decision=decision,
                    summary=decision.reason,
                )
            )
        return out

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
        # LIST_ON_MARKET löst kein sofortiges Debt-Relief aus (Cash kommt
        # frühestens in 24 h), deshalb halbieren wir den Debt-Relief-Bonus
        # für Listings. Direktverkauf (SELL) profitiert voll.
        raw_adjustment = evaluate_sell(
            budget=context.budget,
            sell_price=Decimal(candidate.decision.price),
            squad_size=len(context.squad.players),
            now=context.now,
            next_matchday_start=context.next_matchday_start,
        )
        adjustment = (
            _dampen_debt_relief(raw_adjustment)
            if candidate.decision.action is TradeAction.LIST_ON_MARKET
            else raw_adjustment
        )
        adjusted = _adjust_candidate(candidate, adjustment)
        if any("Debt-Relief" in r for r in adjustment.reasons):
            adjusted = _with_intent(adjusted, TradeIntent.DEBT_RELIEF)
        return adjusted

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


# -- Freie Hilfsfunktionen ------------------------------------------------


def _derive_buy_intent(features: ScoreFeatures, *, squad_size: int) -> TradeIntent:
    """Ordnet einem BUY den dominanten Kaufgrund zu.

    Reihenfolge: SQUAD_FILL (Bedarf) > PROFIT (starker Uptrend) > POINTS
    (Form/Preis-Effizienz als Default). Der LLM-Kurator darf das später
    überschreiben, wenn er einen anderen Grund plausibler findet.
    """

    if squad_size < _SQUAD_FILL_TRIGGER:
        return TradeIntent.SQUAD_FILL
    if features.market_trend >= _PROFIT_TREND_THRESHOLD:
        return TradeIntent.PROFIT
    if features.form >= _POINTS_FORM_THRESHOLD:
        return TradeIntent.POINTS
    # Fallback: kein deutliches Signal → PROFIT vs POINTS entscheidet der stärkere Wert.
    if features.market_trend >= features.form:
        return TradeIntent.PROFIT
    return TradeIntent.POINTS


def _squad_fill_bonus(squad_size: int) -> float:
    """Linearer BUY-Bonus, sobald der Kader unterhalb der Ziel-Größe liegt.

    Bei ≤ (Trigger - 3) Spielern greift der volle Bonus (0.15). Ab dem Trigger
    (13) wird nichts mehr addiert — dann sind wir nah am Cap 15 und wollen
    nicht künstlich pushen.
    """

    if squad_size >= _SQUAD_FILL_TRIGGER:
        return 0.0
    span = _SQUAD_FILL_TRIGGER - max(squad_size, 0)
    max_span = _SQUAD_FILL_TRIGGER
    return _SQUAD_FILL_MAX_BONUS * min(1.0, span / max_span)


def _compute_overbid_price(*, market_price: Decimal, utility: float, spendable: int) -> Decimal:
    """Skaliert das Gebot um bis zu 15 % über Marktpreis (Cap = spendable).

    Ab `_OVERBID_UTILITY_FLOOR` steigt der Aufschlag linear bis zum Cap. Damit
    verliert der Bot nicht dauernd Schlüsselkandidaten an konkurrierende
    Manager, ohne bei mittelmäßigen Kandidaten Geld zu verbrennen.
    """

    if utility <= _OVERBID_UTILITY_FLOOR:
        return market_price
    scale = min(1.0, (utility - _OVERBID_UTILITY_FLOOR) / (1.0 - _OVERBID_UTILITY_FLOOR))
    uplift = Decimal(str(1.0 + _OVERBID_MAX_PCT * scale))
    target = (market_price * uplift).quantize(Decimal("1"))
    cap = Decimal(spendable)
    if target > cap:
        return cap
    return target


def _relative_gain(*, current: Decimal, buy_price: Decimal) -> float:
    if buy_price <= 0:
        return 0.0
    return float((current - buy_price) / buy_price)


def _profit_exit_bonus(gain_ratio: float) -> float:
    """Bonus wächst linear zwischen +10 % und +30 % Gewinn bis zum Cap 0.35."""

    span = _PROFIT_EXIT_MAX_GAIN - _PROFIT_EXIT_MIN_GAIN
    ratio = min(1.0, max(0.0, (gain_ratio - _PROFIT_EXIT_MIN_GAIN) / span))
    return _PROFIT_EXIT_MAX_BONUS * ratio


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
        intent=TradeIntent.PROFIT if ratio > 1.0 else TradeIntent.POINTS,
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
        intent=candidate.decision.intent,
    )
    return HeuristicCandidate(
        id=candidate.id,
        utility=new_utility,
        decision=new_decision,
        summary=f"{candidate.summary}{rule_note}",
    )


def _with_intent(candidate: HeuristicCandidate, intent: TradeIntent) -> HeuristicCandidate:
    if candidate.decision.intent is intent:
        return candidate
    new_decision = TradeDecision(
        action=candidate.decision.action,
        reason=candidate.decision.reason,
        player_id=candidate.decision.player_id,
        player_name=candidate.decision.player_name,
        price=candidate.decision.price,
        offer_id=candidate.decision.offer_id,
        intent=intent,
    )
    return HeuristicCandidate(
        id=candidate.id,
        utility=candidate.utility,
        decision=new_decision,
        summary=candidate.summary,
    )


def _score_reason(action: str, player: Player, score: float, features: ScoreFeatures) -> str:
    return (
        f"{action} {player.last_name}: Score={score:.2f} "
        f"(Form={features.form:.2f}, Preis-Eff={features.price_efficiency:.2f}, "
        f"Trend={features.market_trend:.2f})."
    )


def _buy_reason(
    player: Player,
    score: float,
    features: ScoreFeatures,
    bid_price: Decimal,
    intent: TradeIntent,
) -> str:
    return (
        f"BUY {player.last_name} [{intent.value}] für {int(bid_price):,}: "
        f"Score={score:.2f} (Form={features.form:.2f}, "
        f"Preis-Eff={features.price_efficiency:.2f}, Trend={features.market_trend:.2f})."
    )


def _full_name(player: Player) -> str:
    return f"{player.first_name} {player.last_name}".strip()


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _listing_wish_price(market_value: Decimal) -> Decimal:
    """Wunschpreis fürs Listing: Marktwert * (1 + Uplift), auf ganze Euro gerundet."""

    uplift = Decimal(str(1.0 + _LISTING_UPLIFT_PCT))
    return (market_value * uplift).quantize(Decimal("1"))


def _listing_expected_bonus() -> float:
    """Erwartungswert-Vorteil des Listings ggü. Direktverkauf zum Marktwert.

    Modell: mit Wahrscheinlichkeit p verkauft ein Bieter zum Wunschpreis
    (MW * 1.10), sonst greift der Stale-Fallback zum Marktwert. Erwarteter
    Mehr­erlös = p * 10 %. Bei p=0.5 also +5 % → Bonus 0.05 auf die Utility.
    """

    return _LISTING_UPLIFT_PCT * _LISTING_SELL_PROBABILITY


def _dampen_debt_relief(adjustment: RuleAdjustment) -> RuleAdjustment:
    """Halbiert Debt-Relief-Anteile — für LIST_ON_MARKET, wo Cash erst später fließt."""

    if adjustment.delta == 0.0 or not any("Debt-Relief" in r for r in adjustment.reasons):
        return adjustment
    return RuleAdjustment(
        delta=adjustment.delta * 0.5,
        reasons=tuple(
            r + " (Listing: Cash-Effekt verzögert, halber Bonus)" if "Debt-Relief" in r else r
            for r in adjustment.reasons
        ),
    )


def _stale_reason(now: datetime, record: ListingRecord) -> str | None:
    """Liefert einen menschenlesbaren Grund, wenn das Listing als stale gilt.

    Kriterien (ODER):
    1. Kickbase liefert `expires_at` und es sind <2 h bis Ablauf.
    2. Der letzte LIST_ON_MARKET-Log-Eintrag ist ≥24 h alt.
    """

    if record.expires_at is not None:
        remaining = record.expires_at - now
        if remaining <= _STALE_LISTING_DEADLINE_WINDOW:
            hours_left = max(0.0, remaining.total_seconds() / 3600)
            return f"noch {hours_left:.1f} h bis Listing-Ablauf"
    if record.listed_at is not None:
        age = now - record.listed_at
        if age >= _STALE_LISTING_AGE:
            hours = age.total_seconds() / 3600
            return f"{hours:.1f} h offen"
    return None


def _lookup_squad_name(context: DecisionContext, player_id: str) -> str | None:
    for sp in context.squad.players:
        if sp.player.id == player_id:
            return _full_name(sp.player)
    return None


__all__ = ["HeuristicCandidate", "HeuristicDecisionEngine", "ScoreWeights"]
