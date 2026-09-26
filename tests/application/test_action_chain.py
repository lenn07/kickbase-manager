"""Mehrere Aktionen pro Tick im Deadline-Fenster (P2-16).

Am Freitagabend um 20:00 ist „verkaufen, aufstellen, nachkaufen" **eine**
Handlung: um 20:30 friert die Aufstellung ein und das Konto muss im Plus sein.
Mit einer Aktion pro Tick war das strukturell unmöglich — der nächste Tick kam
zu spät.

Die Kette ist deshalb eng begrenzt: nur im Fenster, höchstens drei Aktionen,
jede für sich gültig auf dem Zustand nach den vorherigen, Abbruch beim ersten
Fehler. Diese vier Grenzen prüfen die Tests hier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from app.application.ai_decision_engine import (
    MAX_ACTIONS_PER_TICK,
    AiDecisionEngine,
)
from app.application.decision_engine import DecisionContext
from app.domain.lineup import DEFAULT_FORMATION
from app.domain.models import (
    LeagueConstraints,
    LeagueMe,
    MarketPlayer,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.domain.trade import TradeAction

from tests.application.test_ai_decision_engine import FakeLlm

LEAGUE_ID = "L1"
MANAGER_ID = "M1"
NOW = datetime(2026, 10, 9, 17, 30, tzinfo=UTC)
# Anpfiff in 60 Minuten → `trading.phase == "deadline"`.
KICKOFF = NOW + timedelta(minutes=60)


def _player(pid: str, *, mv: int = 5_000_000, pos: Position = Position.MIDFIELDER) -> Player:
    return Player(
        id=pid,
        first_name="",
        last_name=f"Spieler {pid}",
        team_id="2",
        position=pos,
        status=PlayerStatus.FIT,
        market_value=Decimal(mv),
        average_points=100.0,
        total_points=400,
    )


def _context(
    *,
    squad_size: int = 3,
    squad_limit: int | None = 3,
    minutes_until_kickoff: int = 60,
) -> DecisionContext:
    squad = tuple(SquadPlayer(player=_player(f"s{i}"), lineup_order=i) for i in range(squad_size))
    market = tuple(
        MarketPlayer(
            player=_player(f"m{i}", mv=6_000_000),
            price=Decimal(6_000_000),
            expires_in_s=7200,
            seller_id="fremd",
        )
        for i in range(2)
    )
    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal(-2_000_000)),
        squad=Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=squad),
        market=market,
        budget=Decimal(-2_000_000),
        min_action_score=0.6,
        max_trade_pct=0.5,
        min_cash_reserve=0,
        now=NOW,
        next_matchday_start=NOW + timedelta(minutes=minutes_until_kickoff),
        interval_min=120,
        max_negative_allowed=Decimal(-40_000_000),
        current_balance_after_open_bids=Decimal(-2_000_000),
        constraints=LeagueConstraints(squad_limit=squad_limit),
    )


def _main_action(action: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "player_id": "s0",
        "price": 0,
        "intent": "DEBT_RELIEF",
        "confidence": 0.9,
        "reason_short": "kurz",
        "reason_long": "lang",
        "expected_outcome": {
            "points_delta_next_matchday": 0,
            "profit_estimate": 0,
            "balance_after_action": 0,
            "balance_after_open_bids": 0,
        },
        "risk_flags": [],
    }
    payload.update(extra)
    return payload


async def _decide(
    monkeypatch: pytest.MonkeyPatch, tool_input: dict[str, Any], context: DecisionContext
):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.application.ai_decision_engine.get_cached_system_prompt", lambda: "SYS"
    )
    engine = AiDecisionEngine(llm=FakeLlm(response=tool_input), api_key="k")
    return await engine.decide(context)


async def test_sell_then_buy_is_one_chain_in_the_deadline_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Fall, für den P2-16 gebaut wurde.

    Kader voll (3/3), Konto im Minus, Anpfiff in einer Stunde. Verkaufen
    schafft Geld **und** den Platz; der Nachkauf braucht beides. Mit einer
    Aktion pro Tick wäre der Kauf erst nach dem Anpfiff möglich.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {
                    "action": "BUY",
                    "player_id": "m0",
                    "price": 6_000_000,
                    "intent": "POINTS",
                    "reason_short": "Platz aus Schritt 1 nutzen",
                }
            ],
        ),
        _context(),
    )

    assert decision.action is TradeAction.SELL
    assert [step.action for step in decision.chain] == [TradeAction.SELL, TradeAction.BUY]
    assert decision.follow_ups[0].player_id == "m0"
    assert decision.follow_ups[0].price == Decimal(6_000_000)


async def test_the_freed_slot_is_visible_to_the_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Fortschreibung würde die Slot-Sperre genau diese Kette blocken.

    Der Kader ist mit 3/3 voll — ein `BUY` allein wäre unzulässig und würde
    zu `HOLD`. Nach dem Verkauf ist der Platz frei, und die Prüfung der
    Folgeaktion muss auf diesem Zustand rechnen.
    """
    without_sell = await _decide(
        monkeypatch,
        _main_action("BUY", player_id="m0", price=6_000_000, intent="POINTS"),
        _context(),
    )
    assert without_sell.action is TradeAction.HOLD
    assert "Kader ist voll" in without_sell.reason

    with_sell = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "ok"}
            ],
        ),
        _context(),
    )
    assert len(with_sell.follow_ups) == 1


async def test_a_second_buy_without_a_slot_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Verkauf schafft **einen** Platz, nicht zwei.

    Die Fortschreibung darf die Prüfung nicht aushebeln — sonst wäre die
    Kette ein Weg, die Slot-Sperre zu umgehen.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "eins"},
                {"action": "BUY", "player_id": "m1", "price": 6_000_000, "reason_short": "zwei"},
            ],
        ),
        _context(),
    )
    # Die ganze Antwort wird verworfen: eine Kette, deren zweiter Kauf nicht
    # gedeckt ist, würde zur Hälfte laufen und die Hälfte als Fehler enden.
    assert decision.action is TradeAction.HOLD
    assert "Kader ist voll" in decision.reason


async def test_follow_ups_are_dropped_outside_the_deadline_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drei Stunden vor Anpfiff kommt ein nächster Tick — eine Aktion reicht.

    Verworfen, nicht abgelehnt: die Hauptaktion ist meist richtig, und sie
    mitzureissen wäre teurer als das Ignorieren des Extras.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "x"}
            ],
        ),
        _context(minutes_until_kickoff=180),
    )
    assert decision.action is TradeAction.SELL
    assert decision.follow_ups == ()


async def test_the_chain_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Höchstens drei Aktionen — der Rest fällt weg statt zu laufen."""
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "SELL_INSTANT", "player_id": "s1", "reason_short": "zwei"},
                {"action": "SELL_INSTANT", "player_id": "s2", "reason_short": "drei"},
                {"action": "SELL_LIST", "player_id": "s2", "price": 1, "reason_short": "vier"},
            ],
        ),
        _context(squad_size=4, squad_limit=4),
    )
    assert len(decision.chain) == MAX_ACTIONS_PER_TICK


async def test_hold_never_carries_follow_ups(monkeypatch: pytest.MonkeyPatch) -> None:
    """„Dieser Tick tut nichts" und „danach noch etwas" widersprechen sich."""
    decision = await _decide(
        monkeypatch,
        _main_action(
            "HOLD",
            player_id="",
            follow_up_actions=[
                {"action": "BUY", "player_id": "m0", "price": 6_000_000, "reason_short": "x"}
            ],
        ),
        _context(),
    )
    assert decision.action is TradeAction.HOLD
    assert decision.follow_ups == ()


async def test_set_lineup_works_as_a_follow_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der eigentliche Zweck: verkaufen **und** die Lücke in der Elf schliessen."""
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {
                    "action": "SET_LINEUP",
                    "reason_short": "Elf ohne den Verkauften",
                    "lineup": {"formation": DEFAULT_FORMATION, "player_ids": ["s1", "s2"]},
                }
            ],
        ),
        _context(),
    )
    follow_up = decision.follow_ups[0]
    assert follow_up.action is TradeAction.SET_LINEUP
    assert follow_up.lineup is not None
    assert follow_up.lineup.player_ids == ("s1", "s2")


async def test_selling_the_same_player_twice_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nach dem ersten Verkauf steht er nicht mehr im Kader.

    Fällt ohne Sonderregel auf: die Fortschreibung entfernt ihn, und die
    bestehende Kaderprüfung schlägt an.
    """
    decision = await _decide(
        monkeypatch,
        _main_action(
            "SELL_INSTANT",
            player_id="s0",
            follow_up_actions=[
                {"action": "SELL_INSTANT", "player_id": "s0", "reason_short": "nochmal"}
            ],
        ),
        _context(),
    )
    assert decision.action is TradeAction.HOLD
    assert "nicht im Kader" in decision.reason
