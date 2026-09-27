"""Entscheidungs-Engine (ADR-5).

Im Betrieb steckt genau eine Implementierung dahinter: `AiDecisionEngine`
(Master-Prompt, Tool-Use). `HoldOnlyDecisionEngine` ist der Fallback für den
Fall, dass kein entschlüsselbarer Anthropic-Key vorliegt — er hält den Tick
auf HOLD, statt ihn scheitern zu lassen.

`DecisionContext` ist die Schnittstelle zwischen Tick und Engine: alles, was
die Engine sehen darf, steht hier und sonst nirgends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.application.player_enrichment import PlayerEnrichment
from app.domain.fixtures import TeamOutlook
from app.domain.lineup import Lineup
from app.domain.models import LeagueConstraints, LeagueMe, LeagueRanking, MarketPlayer, Squad
from app.domain.trade import TradeAction, TradeDecision, TradeIntent


@dataclass(frozen=True, slots=True)
class BuyRecord:
    """Historischer Kauf eines Spielers — Basis für die PROFIT-Exit-Logik."""

    intent: TradeIntent
    buy_price: Decimal
    # Wann der Kauf im trade_log steht. Daraus wird `days_held` im USER-JSON:
    # ein Trade, der seit acht Tagen ohne Zuwachs liegt, bindet einen der 16
    # Kaderplätze, ohne Rendite zu bringen — und das ist beim Kickbase-Trading
    # der eigentliche Kostenfaktor, nicht das Geld. `None` bei Käufen ohne
    # Log-Eintrag (zugelost oder über die App gekauft).
    bought_at: datetime | None = None


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
    # Wie viele **eingehende** Gebote (`ofc`). Auf einem eigenen Listing zeigt
    # Kickbase alle Gebote, die andere Manager abgegeben haben — anders als auf
    # fremden Listings, wo `ofc` nur die eigenen zählt (siehe `MarketPlayer`).
    # `has_offers` sagt nur ob, das hier sagt wie stark: der Unterschied
    # zwischen „einer beißt an" und „vier bieten sich hoch" entscheidet, ob man
    # das Listing hält oder den Preis nachzieht.
    offer_count: int = 0


@dataclass(frozen=True, slots=True)
class OpenBid:
    """Ein eigenes Gebot, das noch auf seinen Zuschlag wartet.

    Kickbase entscheidet erst beim Ablauf des Listings, und zwar zugunsten des
    höchsten Gebots (bei Gleichstand: des früheren). Bis dahin ist das Geld
    gebunden, der Spieler aber noch nicht da — und ein zweites Gebot auf
    denselben Spieler ist kein zusätzlicher Kauf, sondern eine Erhöhung.
    """

    player_id: str
    price: Decimal
    placed_at: datetime


@dataclass(frozen=True, slots=True)
class RecentAction:
    """Kompakte trade_log-Zeile für den AI-Only-Prompt (`recent_actions`)."""

    ts: datetime
    action: TradeAction
    player_id: str | None
    price: Decimal | None
    intent: TradeIntent | None
    executed: bool


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Alles, was die Engine für einen Tick sehen darf."""

    league_id: str
    league_me: LeagueMe
    squad: Squad
    market: tuple[MarketPlayer, ...]
    budget: Decimal
    max_trade_pct: float
    min_cash_reserve: int
    blacklist: tuple[str, ...] = ()
    # Kickbase-Regel-Kontext (Konto/33 %/Startelf/Deadline) — optional, damit
    # ältere Aufrufe (Tests) ohne Wert weiter funktionieren.
    team_value: Decimal = Decimal(0)
    open_bids_total: Decimal = Decimal(0)
    # Die eigenen laufenden Gebote je Spieler. Ohne sie bietet der Bot jeden
    # Tick erneut auf denselben Spieler, weil er sein eigenes Gebot nicht sieht
    # (Defekt D3) — im Betrieb am 2026-09-24 siebenmal auf denselben.
    open_bids: Mapping[str, OpenBid] = field(default_factory=dict)
    now: datetime | None = None
    next_matchday_start: datetime | None = None
    # Nächster Marktwert-Update-Zeitpunkt (`mvud` aus dem Market-Root, täglich
    # 22:00 Berlin). Die zweite Uhr neben der Spieltags-Deadline: sie ist der
    # einzige wirtschaftlich relevante Zeitpunkt des Tages — Käufe davor nehmen
    # die Bewegung mit, Verkäufe danach realisieren sie.
    mv_update_at: datetime | None = None
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
    # Pro Spieler angereicherte Signale (Trend, Startelf, Injury-Label,
    # avg_points_last5) — vom `PlayerEnricher` gefüllt, gecacht in der DB.
    enrichment: Mapping[str, PlayerEnrichment] = field(default_factory=dict)
    # Letzte N Tick-Aktionen für den `recent_actions`-Block im Master-Prompt —
    # aufsteigend sortiert (älteste zuerst). Standard leer, damit Legacy-Tests
    # ohne Trade-Log-Setup unverändert laufen.
    recent_actions: Sequence[RecentAction] = ()
    # Maximaler zulässiger Kontostand-Minusbetrag (33 %-Regel). Bereits negativ
    # ausgedrückt, z. B. Decimal(-42_372_000). Bei Legacy-Aufrufen 0 → wirkt
    # de facto als „kein Minus erlaubt" (konservativ).
    max_negative_allowed: Decimal = Decimal(0)
    # Kontostand nach Abzug aller offenen Gebote (Worst-Case-Bedeckung).
    current_balance_after_open_bids: Decimal = Decimal(0)
    # Aktuelle Aufstellung laut Kickbase (Formation + besetzte Slots). Ohne die
    # Formation kann das Modell keine gültige `SET_LINEUP`-Aktion formulieren —
    # es wüsste nicht, wie viele Verteidiger überhaupt erlaubt sind.
    lineup: Lineup | None = None
    # Aufstellungs-Deadline (`lis` aus `/lineup/overview`, in der Praxis der
    # Spieltagsstart). Ab hier friert Kickbase die Elf ein.
    lineup_deadline: datetime | None = None
    # Liga-Limits (P1-9). Default: ein Objekt, in dem alles `None` ist — also
    # „nichts bekannt", nicht „keine Limits". Der Unterschied entscheidet, ob
    # das Modell einen Verstoß behauptet oder Unwissen einräumt.
    constraints: LeagueConstraints = field(default_factory=LeagueConstraints)
    # Gegner, Heimrecht und Schwierigkeit je **Verein** (P2-11). Nach `team_id`
    # indiziert, nicht nach Spieler: der Spielplan gilt für die Mannschaft, und
    # elf Kaderspieler desselben Clubs teilen ihn. Leeres Mapping = kein
    # Spielplan bekannt; der Payload setzt dann `missing_data:fixtures`.
    team_outlook: Mapping[str, TeamOutlook] = field(default_factory=dict)
    # Ligatabelle: Rang, Rueckstand, Restspieltage (P2-12). `None` = nicht
    # geladen; der Payload setzt dann `missing_data:league_ranking`. Ohne diese
    # Zahlen kann das Modell seinen Risikoappetit nicht wählen — 3000 Punkte
    # Rueckstand an Spieltag 5 verlangen etwas anderes als an Spieltag 30.
    league_ranking: LeagueRanking | None = None


class DecisionEngine(Protocol):
    async def decide(self, context: DecisionContext) -> TradeDecision: ...


class HoldOnlyDecisionEngine:
    """Fallback ohne nutzbaren Anthropic-Key — hält den Tick auf HOLD.

    Der Grund steht als Klartext im `trade_log`, damit im Dashboard sichtbar
    ist, warum nichts passiert: nicht „das Modell wollte nicht", sondern „es
    wurde gar nicht gefragt".
    """

    _REASON = "HOLD: Kein nutzbarer Anthropic-Key — das Modell wurde nicht befragt."

    async def decide(self, context: DecisionContext) -> TradeDecision:
        del context  # Ohne Key gibt es nichts auszuwerten.
        return TradeDecision.hold(self._REASON)
