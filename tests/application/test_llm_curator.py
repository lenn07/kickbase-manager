"""Unit-Tests für den LlmCurator (Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from app.application.decision_engine import DecisionContext
from app.application.heuristic_engine import HeuristicCandidate
from app.application.llm_curator import LlmCurator
from app.domain.models import LeagueMe, Squad
from app.domain.trade import TradeAction, TradeDecision, TradeIntent
from app.infrastructure.llm.anthropic_client import LlmChatError

LEAGUE_ID = "L1"
MANAGER_ID = "M1"


@dataclass
class FakeHeuristic:
    """Duck-typed Fake — der LlmCurator ruft ausschließlich `propose()`."""

    candidates: tuple[HeuristicCandidate, ...] = ()
    calls: int = 0

    async def propose(self, context: DecisionContext) -> tuple[HeuristicCandidate, ...]:
        del context
        self.calls += 1
        return self.candidates


class FakeLlmChat:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def select_action(
        self,
        *,
        api_key: str,
        system_prompt: str,
        user_message: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 512,
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


def _context(
    *,
    budget: Decimal = Decimal("10000000"),
    min_action_score: float = 0.6,
) -> DecisionContext:
    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=budget),
        squad=Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=()),
        market=(),
        budget=budget,
        min_action_score=min_action_score,
        max_trade_pct=0.5,
        min_cash_reserve=0,
    )


def _buy_candidate(
    utility: float = 0.9, intent: TradeIntent = TradeIntent.POINTS
) -> HeuristicCandidate:
    decision = TradeDecision(
        action=TradeAction.BUY,
        reason="BUY Musterspieler: Score=0.90",
        player_id="m1",
        player_name="Musterspieler",
        price=Decimal("2000000"),
        intent=intent,
    )
    return HeuristicCandidate(
        id="BUY:m1", utility=utility, decision=decision, summary=decision.reason
    )


def _sell_candidate(utility: float = 0.55) -> HeuristicCandidate:
    decision = TradeDecision(
        action=TradeAction.SELL,
        reason="SELL Weakling: Score=0.20",
        player_id="s1",
        player_name="Weakling",
        price=Decimal("500000"),
    )
    return HeuristicCandidate(
        id="SELL:s1", utility=utility, decision=decision, summary=decision.reason
    )


async def test_llm_pick_valid_candidate_returns_that_decision() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(), _sell_candidate()))
    llm = FakeLlmChat(response={"candidate_id": "BUY:m1", "reason": "Form top."})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.BUY
    assert decision.player_id == "m1"
    assert "LLM: Form top." in decision.reason
    assert len(llm.calls) == 1


async def test_llm_pick_hold_returns_hold_decision() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(utility=0.65),))
    llm = FakeLlmChat(response={"candidate_id": "HOLD:0", "reason": "Kein klarer Mehrwert."})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert "Kein klarer Mehrwert." in decision.reason


async def test_invalid_candidate_id_falls_back_to_heuristic_best() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(), _sell_candidate()))
    llm = FakeLlmChat(response={"candidate_id": "BOGUS", "reason": "n/a"})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.BUY
    assert decision.player_id == "m1"
    assert "Fallback" in decision.reason
    assert "BOGUS" in decision.reason


async def test_llm_chat_error_falls_back_to_heuristic() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(),))
    llm = FakeLlmChat(error=LlmChatError("anthropic down"))
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.BUY
    assert "anthropic down" in decision.reason


async def test_no_heuristic_candidates_short_circuits_to_hold() -> None:
    heuristic = FakeHeuristic(candidates=())
    llm = FakeLlmChat(response={"candidate_id": "HOLD:0", "reason": "should not be called"})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert llm.calls == []


async def test_user_message_lists_all_candidates_and_hold_baseline() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(), _sell_candidate()))
    llm = FakeLlmChat(response={"candidate_id": "HOLD:0", "reason": "warten"})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    await curator.decide(_context(min_action_score=0.7))

    assert len(llm.calls) == 1
    prompt = llm.calls[0]["user_message"]
    assert "BUY:m1" in prompt
    assert "SELL:s1" in prompt
    assert "HOLD:0" in prompt
    assert "0.70" in prompt


@pytest.mark.parametrize("bad", [None, 123, {"nested": "value"}])
async def test_non_string_candidate_id_falls_back(bad: object) -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(),))
    llm = FakeLlmChat(response={"candidate_id": bad, "reason": "x"})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.BUY
    assert "Fallback" in decision.reason


async def test_llm_keeps_heuristic_intent_when_not_overridden() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(intent=TradeIntent.SQUAD_FILL),))
    llm = FakeLlmChat(response={"candidate_id": "BUY:m1", "reason": "passt."})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.BUY
    assert decision.intent is TradeIntent.SQUAD_FILL


async def test_llm_can_override_intent() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(intent=TradeIntent.POINTS),))
    llm = FakeLlmChat(
        response={
            "candidate_id": "BUY:m1",
            "reason": "starke Steigung erwartet.",
            "intent": "PROFIT",
        }
    )
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.intent is TradeIntent.PROFIT
    assert "Intent-Override" in decision.reason


async def test_llm_invalid_intent_falls_back_to_heuristic_intent() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(intent=TradeIntent.SQUAD_FILL),))
    llm = FakeLlmChat(
        response={
            "candidate_id": "BUY:m1",
            "reason": "ok",
            "intent": "BOGUS",
        }
    )
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.intent is TradeIntent.SQUAD_FILL


async def test_user_message_lists_intent_per_candidate() -> None:
    heuristic = FakeHeuristic(candidates=(_buy_candidate(intent=TradeIntent.SQUAD_FILL),))
    llm = FakeLlmChat(response={"candidate_id": "HOLD:0", "reason": "warten"})
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    await curator.decide(_context())

    prompt = llm.calls[0]["user_message"]
    assert "intent=SQUAD_FILL" in prompt


async def test_hold_from_heuristic_fallback_preserves_hold_action() -> None:
    hold_from_heuristic = HeuristicCandidate(
        id="HOLD:0",
        utility=0.5,
        decision=TradeDecision.hold("Heuristik-HOLD"),
        summary="Heuristik-HOLD",
    )
    heuristic = FakeHeuristic(candidates=(hold_from_heuristic,))
    llm = FakeLlmChat(error=LlmChatError("boom"))
    curator = LlmCurator(heuristic=heuristic, llm=llm, api_key="sk-ant-good")

    decision = await curator.decide(_context())

    assert decision.action is TradeAction.HOLD
    assert "boom" in decision.reason
