"""Entscheidungs-Engine (ADR-5).

Phase 3 liefert einen Stub, der immer `HOLD` zurückgibt — damit der
Scheduler-Pfad End-to-End getestet werden kann. Phase 4 ersetzt die Impl durch
die Heuristik-Schicht, Phase 5 verdrahtet den LLM-Kurator obendrauf.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.domain.models import LeagueMe, MarketPlayer, Squad
from app.domain.trade import TradeDecision, TradeIntent


@dataclass(frozen=True, slots=True)
class BuyRecord:
    """Historischer Kauf eines Spielers — Basis für die PROFIT-Exit-Logik."""

    intent: TradeIntent
    buy_price: Decimal


@dataclass(frozen=True, slots=True)
class ListingRecord:
    """Aktives eigenes Transfermarkt-Listing — Basis für den Stale-Fallback.

    `listed_at` kommt aus dem trade_log (letzter LIST_ON_MARKET-Tick),
    `expires_at` liefert Kickbase im Market-Response. Beide dürfen None sein
    (frisch neu geladenes Listing ohne Log-Historie, oder Kickbase liefert
    keine Ablaufzeit); in dem Fall greift die jeweils andere Quelle.
    """

    player_id: str
    listing_price: Decimal
    listed_at: datetime | None
    expires_at: datetime | None
    has_offers: bool


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Alles, was die Engine für einen Tick sehen darf."""

    league_id: str
    league_me: LeagueMe
    squad: Squad
    market: tuple[MarketPlayer, ...]
    budget: Decimal
    min_action_score: float
    max_trade_pct: float
    min_cash_reserve: int
    blacklist: tuple[str, ...] = ()
    # Kickbase-Regel-Kontext (Konto/33 %/Startelf/Deadline) — optional, damit
    # ältere Aufrufe (Tests) ohne Wert weiter funktionieren.
    team_value: Decimal = Decimal(0)
    open_bids_total: Decimal = Decimal(0)
    now: datetime | None = None
    next_matchday_start: datetime | None = None
    # Scheduler-Intervall in Minuten — für die Berechnung, wie viele Ticks
    # bis zum nächsten Spieltag noch reinpassen (dynamische Aktions-Schwelle).
    interval_min: int = 120
    # Historische BUYs pro player_id (Intent + Kaufpreis) — steuert
    # PROFIT-Exits und wird vom RunTickUseCase aus dem trade_log befüllt.
    buy_history: Mapping[str, BuyRecord] = field(default_factory=dict)
    # Eigene aktive Listings pro player_id (Preis + Zeitstempel) — Basis für
    # Stale-Fallback + Doppel-Listing-Vermeidung. Der RunTickUseCase liest
    # `seller_id == manager_id`-Einträge aus dem Market-Response und mischt
    # sie mit dem letzten LIST_ON_MARKET-Timestamp aus dem trade_log.
    own_listings: Mapping[str, ListingRecord] = field(default_factory=dict)


class DecisionEngine(Protocol):
    async def decide(self, context: DecisionContext) -> TradeDecision: ...


class HoldOnlyDecisionEngine:
    """Phase-3-Stub: bewusst konservativ, wartet auf Phase 4/5."""

    _REASON = "Phase-3-Stub: Heuristik + LLM sind noch nicht aktiv."

    async def decide(self, context: DecisionContext) -> TradeDecision:
        del context  # Kontext wird erst in Phase 4/5 ausgewertet.
        return TradeDecision.hold(self._REASON)
