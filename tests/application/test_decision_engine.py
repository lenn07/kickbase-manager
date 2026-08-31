from __future__ import annotations

from decimal import Decimal

import pytest
from app.application.decision_engine import DecisionContext, HoldOnlyDecisionEngine
from app.domain.models import LeagueMe, Squad
from app.domain.trade import TradeAction


@pytest.fixture
def context() -> DecisionContext:
    return DecisionContext(
        league_id="L1",
        league_me=LeagueMe(league_id="L1", budget=Decimal(10_000)),
        squad=Squad(league_id="L1", manager_id="u1", players=()),
        market=(),
        budget=Decimal(10_000),
        min_action_score=0.6,
        max_trade_pct=0.25,
        min_cash_reserve=0,
    )


async def test_hold_only_engine_always_returns_hold(context: DecisionContext) -> None:
    engine = HoldOnlyDecisionEngine()
    decision = await engine.decide(context)
    assert decision.action is TradeAction.HOLD
    assert decision.is_hold
    assert "Phase" in decision.reason
