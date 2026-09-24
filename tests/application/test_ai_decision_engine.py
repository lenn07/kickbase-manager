"""Unit-Tests für die AiDecisionEngine (Master-Prompt-Modus)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.application.ai_decision_engine import AiDecisionEngine, _build_user_payload
from app.application.decision_engine import (
    BuyRecord,
    DecisionContext,
    ListingRecord,
    OpenBid,
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
        temperature: float | None = None,
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
                "temperature": temperature,
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
    open_bids: dict[str, OpenBid] | None = None,
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
        open_bids=open_bids or {},
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
        expires_in_s=None,
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
        expires_in_s=None,
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
        expires_in_s=None,
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
            market_trend_1d_pct=0.4,
            market_trend_3d_pct=1.1,
            market_trend_7d_pct=2.5,
            market_trend_30d_pct=8.3,
            mv_max_30d=5_100_000,
            avg_points_last5=140.0,
            start_probability_next=0.9,
            start_probability_source="kickbase_prob",
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
    assert payload["squad"][0]["market_trend_1d_pct"] == 0.4
    assert payload["squad"][0]["market_trend_30d_pct"] == 8.3
    assert payload["squad"][0]["mv_max_30d"] == 5_100_000
    assert payload["market"][0]["player_id"] == "m1"
    assert payload["recent_actions"][0]["action"] == "BUY"
    assert payload["incoming_offers"][0]["offer_id"] == "off-2"
    assert payload["budget"]["max_negative_allowed"] == -42_000_000
    assert payload["ticks_until_matchday_start"] is not None


async def test_starting_xi_count_reflects_lineup_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    starters = tuple(SquadPlayer(player=_player(f"s{i}"), lineup_order=i) for i in range(10))
    bench = (
        SquadPlayer(player=_player("b1"), lineup_order=15),
        SquadPlayer(player=_player("b2"), lineup_order=None),
    )
    llm = FakeLlm(response={"action": "HOLD", "price": 0, "intent": "NONE"})
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-x")

    await engine.decide(_context(squad_players=starters + bench))

    payload = json.loads(llm.calls[0]["user_message"])
    assert payload["squad_size"] == 12
    assert payload["starting_xi_count"] == 10  # unbesetzter Slot → Startelf-Loch
    starters_in_payload = [e for e in payload["squad"] if e["in_starting_xi"]]
    bench_in_payload = [e for e in payload["squad"] if not e["in_starting_xi"]]
    assert len(starters_in_payload) == 10
    assert len(bench_in_payload) == 2
    assert bench_in_payload[0]["lineup_order"] == 15
    assert bench_in_payload[1]["lineup_order"] is None


# -- P0-2: erfundene IDs kommen nicht durch -------------------------------


async def test_accept_offer_with_an_unknown_offer_id_becomes_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der teuerste Halluzinations-Fall: ein Gebot annehmen, das es nicht gibt.

    Solange der Feldname des Gebots-Arrays offen ist (Plan §8/F1), ist
    `incoming_offers` leer — jede vom Modell genannte `offer_id` ist erfunden.
    Ohne diese Prüfung ginge sie an Kickbase.
    """
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    squad_player = SquadPlayer(player=_player("s1"), lineup_order=0)
    llm = FakeLlm(
        response={
            "action": "ACCEPT_OFFER",
            "player_id": "s1",
            "offer_id": "off_1",
            "price": 0,
            "intent": "PROFIT",
            "confidence": 0.9,
            "reason_short": "gutes Gebot",
            "reason_long": "Preis liegt über dem Zielwert.",
            "expected_outcome": {
                "points_delta_next_matchday": 0,
                "profit_estimate": 1_000_000,
                "balance_after_action": 4_500_000,
                "balance_after_open_bids": 4_500_000,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-test")

    decision = await engine.decide(_context(squad_players=(squad_player,)))

    assert decision.action is TradeAction.HOLD
    assert "unbekannter offer_id" in decision.reason


async def test_buy_for_a_player_who_is_not_on_the_market_becomes_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Gebot auf einen Spieler, der nicht gelistet ist, ist ein toter Tick."""
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(
        response={
            "action": "BUY",
            "player_id": "geistesblitz",
            "price": 5_000_000,
            "intent": "POINTS",
            "confidence": 0.8,
            "reason_short": "stark",
            "reason_long": "Formkurve steigt.",
            "expected_outcome": {
                "points_delta_next_matchday": 40,
                "profit_estimate": 0,
                "balance_after_action": -1_500_000,
                "balance_after_open_bids": -1_500_000,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-test")

    decision = await engine.decide(_context(market=()))

    assert decision.action is TradeAction.HOLD
    assert "nicht am Markt" in decision.reason


async def test_sell_for_a_player_who_is_not_in_the_squad_becomes_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(
        response={
            "action": "SELL_INSTANT",
            "player_id": "fremder",
            "price": 0,
            "intent": "DEBT_RELIEF",
            "confidence": 0.7,
            "reason_short": "Konto ins Plus",
            "reason_long": "Deadline in 40 Minuten.",
            "expected_outcome": {
                "points_delta_next_matchday": -20,
                "profit_estimate": 0,
                "balance_after_action": 500_000,
                "balance_after_open_bids": 500_000,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-test")

    decision = await engine.decide(_context(squad_players=()))

    assert decision.action is TradeAction.HOLD
    assert "nicht im Kader" in decision.reason


async def test_accept_offer_passes_once_the_offer_is_really_in_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Sperre ist kein Verbot, sondern eine Deckungsprüfung.

    Sobald Kickbase echte Gebote liefert (nach F1), muss `ACCEPT_OFFER` wieder
    durchgehen — sonst wäre die Aktion dauerhaft tot.
    """
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    squad_player = SquadPlayer(player=_player("s1"), lineup_order=0)
    offer = MarketOffer(
        id="off_1",
        user_id="8012345",
        user_name="Rival",
        price=Decimal(9_300_000),
        valid_until=None,
    )
    listing = MarketPlayer(
        player=squad_player.player,
        price=Decimal(9_200_000),
        expires_in_s=None,
        seller_id=MANAGER_ID,
        offer_count=1,
        offers=(offer,),
    )
    llm = FakeLlm(
        response={
            "action": "ACCEPT_OFFER",
            "player_id": "s1",
            "offer_id": "off_1",
            "price": 0,
            "intent": "PROFIT",
            "confidence": 0.9,
            "reason_short": "über Zielwert",
            "reason_long": "Gebot liegt über Marktwert.",
            "expected_outcome": {
                "points_delta_next_matchday": 0,
                "profit_estimate": 1_000_000,
                "balance_after_action": 8_900_000,
                "balance_after_open_bids": 8_900_000,
            },
            "risk_flags": [],
        }
    )
    engine = AiDecisionEngine(llm=llm, api_key="sk-ant-test")

    decision = await engine.decide(_context(squad_players=(squad_player,), market=(listing,)))

    assert decision.action is TradeAction.ACCEPT_OFFER
    assert decision.offer_id == "off_1"


async def test_decision_call_runs_at_temperature_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Entscheidung ist kein kreativer Akt — gleiche Lage, gleiche Aktion.

    Ohne `temperature=0` misst jede Eval Sampling statt Prompt-Treue, und ein
    Prompt-Merge lässt sich nicht mehr belegen (Plan §9).
    """
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt",
        lambda: "SYS",
    )
    llm = FakeLlm(
        response={
            "action": "HOLD",
            "price": 0,
            "intent": "NONE",
            "confidence": 0.5,
            "reason_short": "ruhig",
            "reason_long": "Keine Gelegenheit.",
            "expected_outcome": {
                "points_delta_next_matchday": 0,
                "profit_estimate": 0,
                "balance_after_action": 0,
                "balance_after_open_bids": 0,
            },
            "risk_flags": [],
        }
    )

    await AiDecisionEngine(llm=llm, api_key="sk-ant-test").decide(_context())

    assert llm.calls[0]["temperature"] == 0.0


# -- P1-6: Einstand im Payload -------------------------------------------


def _payload_for(squad_players: tuple[SquadPlayer, ...], **kwargs: Any) -> dict[str, Any]:
    return _build_user_payload(_context(squad_players=squad_players, **kwargs))


def test_entry_price_prefers_kickbase_over_the_own_trade_log() -> None:
    """Kickbase kennt den echten Einstand, das eigene Log nur den eigenen Kauf.

    Weichen beide ab (etwa weil der Spieler zwischendurch über die App
    gehandelt wurde), gewinnt die Quelle, die nicht auf den Bot beschränkt ist.
    """
    sp = SquadPlayer(
        player=_player("s1", mv=8_000_000),
        buy_price=Decimal(6_000_000),
        unrealized_pnl=Decimal(2_000_000),
    )
    payload = _payload_for(
        (sp,),
        buy_history={"s1": BuyRecord(intent=TradeIntent.PROFIT, buy_price=Decimal(1))},
    )
    entry = payload["squad"][0]
    assert entry["bought_at_price"] == 6_000_000
    assert entry["unrealized_pnl"] == 2_000_000
    # Der Intent bleibt trotzdem aus dem Log — den liefert Kickbase nicht.
    assert entry["bought_intent"] == "PROFIT"


def test_entry_price_falls_back_to_the_trade_log() -> None:
    """Fehlt `mvgl`, trägt der geloggte eigene Kauf den Einstand weiter."""
    sp = SquadPlayer(player=_player("s1", mv=8_000_000))
    payload = _payload_for(
        (sp,),
        buy_history={"s1": BuyRecord(intent=TradeIntent.POINTS, buy_price=Decimal(7_500_000))},
    )
    entry = payload["squad"][0]
    assert entry["bought_at_price"] == 7_500_000
    assert entry["unrealized_pnl"] is None
    assert "missing_data:bought_at_price" not in entry.get("missing_data_flags", [])


def test_unknown_entry_price_is_flagged_not_zeroed() -> None:
    """Ohne jede Quelle bleibt der Einstand `null` — und sagt das auch.

    Eine 0 hier hieße „umsonst bekommen" und würde den gesamten Marktwert als
    Gewinn ausweisen. Das ist derselbe Fehler wie die Default-0 bei
    `team_value` (Defekt D1), nur pro Spieler.
    """
    payload = _payload_for((SquadPlayer(player=_player("s1")),))
    entry = payload["squad"][0]
    assert entry["bought_at_price"] is None
    assert "missing_data:bought_at_price" in entry["missing_data_flags"]


# -- D3: kein sinnloses Nachbieten ---------------------------------------


def _market(pid: str, mv: int = 10_000_000) -> MarketPlayer:
    return MarketPlayer(
        player=_player(pid, mv=mv), price=Decimal(mv), expires_in_s=3600, seller_id="fremd"
    )


async def _decide(monkeypatch: pytest.MonkeyPatch, tool_input: dict[str, Any], **ctx: Any):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt", lambda: "SYS"
    )
    llm = FakeLlm(response=tool_input)
    engine = AiDecisionEngine(llm=llm, api_key="k")
    return await engine.decide(_context(**ctx))


def _buy(player_id: str, price: int) -> dict[str, Any]:
    return {
        "action": "BUY",
        "player_id": player_id,
        "price": price,
        "intent": "POINTS",
        "confidence": 0.8,
        "reason_short": "kurz",
        "reason_long": "lang",
        "expected_outcome": {
            "points_delta_next_matchday": 10,
            "profit_estimate": 0,
            "balance_after_action": 0,
            "balance_after_open_bids": 0,
        },
        "risk_flags": [],
    }


async def test_rebidding_the_same_amount_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Defekt aus dem Betrieb: siebenmal dasselbe Gebot auf denselben Spieler.

    Kickbase entscheidet erst beim Ablauf des Listings und nimmt das höchste
    Gebot. Ein gleich hohes Nachgebot ändert daran nichts — es verbraucht nur
    den Tick, und der Bot hat davon sieben hintereinander verbraucht.
    """
    decision = await _decide(
        monkeypatch,
        _buy("m1", 9_000_000),
        market=(_market("m1"),),
        open_bids={
            "m1": OpenBid(
                player_id="m1",
                price=Decimal(9_000_000),
                placed_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
            )
        },
    )
    assert decision.action is TradeAction.HOLD
    assert "läuft bereits ein eigenes Gebot" in decision.reason


async def test_raising_an_existing_bid_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nachlegen muss möglich bleiben — sonst verliert der Bot jedes Bietduell.

    Bei Konkurrenz ist ein höheres Gebot der einzige Weg zum Zuschlag. Die
    Sperre darf nur das treffen, was nichts verändert.
    """
    decision = await _decide(
        monkeypatch,
        _buy("m1", 11_000_000),
        market=(_market("m1"),),
        open_bids={
            "m1": OpenBid(
                player_id="m1",
                price=Decimal(9_000_000),
                placed_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
            )
        },
    )
    assert decision.action is TradeAction.BUY
    assert decision.price == Decimal(11_000_000)


async def test_open_bids_are_visible_in_the_payload() -> None:
    """Das Modell muss sehen, dass ein Gebot läuft — sonst bietet es blind erneut."""
    payload = _build_user_payload(
        _context(
            market=(_market("m1"), _market("m2")),
            open_bids={
                "m1": OpenBid(
                    player_id="m1",
                    price=Decimal(9_000_000),
                    placed_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
                )
            },
        )
    )
    by_id = {entry["player_id"]: entry for entry in payload["market"]}
    assert by_id["m1"]["my_open_bid_price"] == 9_000_000
    assert by_id["m1"]["my_bid_placed_at_iso"]
    assert by_id["m2"]["my_open_bid_price"] is None
    assert payload["budget"]["open_bids_count"] == 1
