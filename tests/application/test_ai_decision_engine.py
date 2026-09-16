"""Unit-Tests für die AiDecisionEngine (Master-Prompt-Modus)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.application.ai_decision_engine import AiDecisionEngine
from app.application.decision_engine import (
    BuyRecord,
    DecisionContext,
    ListingRecord,
    RecentAction,
)
from app.application.player_enrichment import PlayerEnrichment
from app.domain.models import (
    LeagueMe,
    MarketOffer,
    MarketPlayer,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.domain.trade import TradeAction, TradeIntent
from app.infrastructure.llm.anthropic_client import LlmChatError

LEAGUE_ID = "L1"
MANAGER_ID = "M1"


class FakeLlm:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def submit_decision(
        self,
        *,
        api_key: str,
        system_prompt: str,
        user_message: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "api_key": api_key,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "tool_name": tool_name,
                "tool_description": tool_description,
                "input_schema": input_schema,
                "max_tokens": max_tokens,
            }
        )
        if self._error is not None:
            raise self._error
        return self._response or {}


def _player(pid: str, *, avg: float = 120.0, mv: int = 5_000_000) -> Player:
    return Player(
        id=pid,
        first_name="Vor",
        last_name=f"Name{pid}",
        team_id="T1",
        position=Position.MIDFIELDER,
        status=PlayerStatus.FIT,
        market_value=Decimal(mv),
        average_points=avg,
        total_points=int(avg) * 20,
    )


def _context(
    *,
    squad_players: tuple[SquadPlayer, ...] = (),
    market: tuple[MarketPlayer, ...] = (),
    recent_actions: tuple[RecentAction, ...] = (),
    buy_history: dict[str, BuyRecord] | None = None,
    own_listings: dict[str, ListingRecord] | None = None,
    enrichment: dict[str, PlayerEnrichment] | None = None,
    open_bids_total: Decimal = Decimal(0),
    max_negative: Decimal = Decimal(-42_000_000),
) -> DecisionContext:
    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal(3_500_000)),
        squad=Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=squad_players),
        market=market,
        budget=Decimal(3_500_000),
        min_action_score=0.6,
        max_trade_pct=0.5,
        min_cash_reserve=0,
        team_value=Decimal(128_000_000),
        open_bids_total=open_bids_total,
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        next_matchday_start=datetime(2026, 9, 18, 18, 30, tzinfo=UTC),
        interval_min=120,
        buy_history=buy_history or {},
        own_listings=own_listings or {},
        enrichment=enrichment or {},
        recent_actions=recent_actions,
        max_negative_allowed=max_negative,
        current_balance_after_open_bids=Decimal(3_500_000) - open_bids_total,
    )


async def test_buy_decision_maps_to_domain_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    market_player = MarketPlayer(
        player=_player("m1"),
        price=Decimal(6_000_000),
        expires_at=None,
        seller_id="other",
        offers=(),
    )
    llm = FakeLlm(
        response={
            "action": "BUY",
            "player_id": "m1",
            "offer_id": "",
            "price": 6_500_000,
            "intent": "POINTS",
            "confidence": 0.72,
            "reason_short": "Startelfsicherer Torschütze",
            "reason_long": "Er trifft konstant, günstiger Overbid rechtfertigt sich.",
            "expected_outcome": {
                "points_delta_next_matchday": 20,
                "profit_estimate": 500_000,
                "balance_after_action": -3_000_000,
                "balance_after_open_bids": -3_000_000,
            },
            "risk_flags": ["deadline_ok"],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context(market=(market_player,)))

    assert decision.action is TradeAction.BUY
    assert decision.player_id == "m1"
    assert decision.player_name == "Vor Namem1"
    assert decision.price == Decimal(6_500_000)
    assert decision.intent is TradeIntent.POINTS
    assert "Startelfsicherer Torschütze" in decision.reason
    assert "Er trifft konstant" in decision.reason
    # Cache-Prefix + Tool wurden korrekt weitergegeben.
    call = llm.calls[0]
    assert call["system_prompt"] == "SYS"
    assert call["tool_name"] == "submit_decision"


async def test_sell_list_maps_to_list_on_market(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    squad_player = SquadPlayer(player=_player("s1"))
    llm = FakeLlm(
        response={
            "action": "SELL_LIST",
            "player_id": "s1",
            "price": 5_500_000,
            "intent": "PROFIT",
            "confidence": 0.6,
            "reason_short": "Peak erreicht",
            "reason_long": "Trend flacht ab, jetzt listen.",
            "expected_outcome": {
                "points_delta_next_matchday": 0,
                "profit_estimate": 400_000,
                "balance_after_action": 3_500_000,
                "balance_after_open_bids": 3_500_000,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context(squad_players=(squad_player,)))

    assert decision.action is TradeAction.LIST_ON_MARKET
    assert decision.price == Decimal(5_500_000)
    assert decision.intent is TradeIntent.PROFIT


async def test_sell_instant_maps_to_sell_and_drops_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    squad_player = SquadPlayer(player=_player("s1"))
    llm = FakeLlm(
        response={
            "action": "SELL_INSTANT",
            "player_id": "s1",
            "price": 5_000_000,
            "intent": "DEBT_RELIEF",
            "confidence": 0.9,
            "reason_short": "Cash brauchen",
            "reason_long": "Nur noch 2 h bis Deadline, Konto negativ.",
            "expected_outcome": {
                "points_delta_next_matchday": -5,
                "profit_estimate": 0,
                "balance_after_action": 5_000_000,
                "balance_after_open_bids": 5_000_000,
            },
            "risk_flags": ["deadline_tight"],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context(squad_players=(squad_player,)))

    assert decision.action is TradeAction.SELL
    assert decision.price is None  # Direktverkauf hat keinen Preis
    assert decision.intent is TradeIntent.DEBT_RELIEF


async def test_hold_returns_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(
        response={
            "action": "HOLD",
            "price": 0,
            "intent": "NONE",
            "confidence": 0.55,
            "reason_short": "Nichts spannend",
            "reason_long": "Alle Kandidaten zu teuer, warten lohnt.",
            "expected_outcome": {
                "points_delta_next_matchday": 0,
                "profit_estimate": 0,
                "balance_after_action": 3_500_000,
                "balance_after_open_bids": 3_500_000,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert decision.intent is None


async def test_llm_error_returns_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(error=LlmChatError("anthropic down"))
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert "anthropic down" in decision.reason


async def test_invalid_action_returns_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(response={"action": "TELEPORT", "price": 0, "intent": "NONE"})
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert "TELEPORT" in decision.reason or "ungültige Antwort" in decision.reason


async def test_buy_without_player_id_returns_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(
        response={
            "action": "BUY",
            "price": 1_000_000,
            "intent": "POINTS",
            "confidence": 0.5,
            "reason_short": "…",
            "reason_long": "…",
            "expected_outcome": {
                "points_delta_next_matchday": 0,
                "profit_estimate": 0,
                "balance_after_action": 0,
                "balance_after_open_bids": 0,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    decision = await engine.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert "player_id" in decision.reason


async def test_user_payload_contains_squad_market_recent_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    squad_player = SquadPlayer(player=_player("s1"))
    market_player = MarketPlayer(
        player=_player("m1"),
        price=Decimal(6_000_000),
        expires_at=None,
        seller_id="other",
        offers=(
            MarketOffer(
                id="off-1",
                user_id=MANAGER_ID,
                user_name="ich",
                price=Decimal(1_200_000),
                valid_until=None,
            ),
        ),
    )
    incoming_market = MarketPlayer(
        player=_player("s1"),
        price=Decimal(5_000_000),
        expires_at=None,
        seller_id=MANAGER_ID,
        offers=(
            MarketOffer(
                id="off-2",
                user_id="rival",
                user_name="Gegner",
                price=Decimal(4_800_000),
                valid_until=datetime.now(UTC) + timedelta(hours=1),
            ),
        ),
    )
    enrichment = {
        "s1": PlayerEnrichment(
            player_id="s1",
            market_trend_7d_pct=2.5,
            avg_points_last5=140.0,
            start_probability_next=0.9,
            injury_status="fit",
            missing_data_flags=("missing_data:avg_points_last5_using_season_avg",),
        ),
    }
    recent = (
        RecentAction(
            ts=datetime(2026, 9, 15, 20, 0, tzinfo=UTC),
            action=TradeAction.BUY,
            player_id="s1",
            price=Decimal(4_800_000),
            intent=TradeIntent.PROFIT,
            executed=True,
        ),
    )
    llm = FakeLlm(response={"action": "HOLD", "price": 0, "intent": "NONE"})
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    await engine.decide(
        _context(
            squad_players=(squad_player,),
            market=(market_player, incoming_market),
            recent_actions=recent,
            enrichment=enrichment,
        )
    )

    payload = json.loads(llm.calls[0]["user_message"])
    assert payload["squad"][0]["player_id"] == "s1"
    assert payload["squad"][0]["market_trend_7d_pct"] == 2.5
    assert payload["market"][0]["player_id"] == "m1"
    assert payload["recent_actions"][0]["action"] == "BUY"
    assert payload["incoming_offers"][0]["offer_id"] == "off-2"
    assert payload["budget"]["max_negative_allowed"] == -42_000_000
    assert payload["ticks_until_matchday_start"] is not None
