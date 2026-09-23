"""Prompt-Eval: echte Modell-Calls gegen feste Szenarien (Testgerüst Stufe 4).

**Kostet Geld.** Läuft nicht im Default-Run — `addopts` filtert `-m "not eval"`.
Vor jedem Merge an `docs/master_prompt.md` einmal ausführen:

    ANTHROPIC_API_KEY=sk-... pytest -m eval

Warum drei Läufe pro Szenario: ein einzelner Lauf kann eine Regelverletzung
verschlucken, die das Modell nur in einem von drei Fällen zeigt. `temperature=0`
allein reicht nicht — die API garantiert keine Bit-Gleichheit.

Warum Aktions-*Mengen* statt einer erwarteten Aktion: siehe `scenarios.py`.
Ein Eval, das eine bestimmte Aktion erzwingt, misst Zufall und ist nach dem
ersten Prompt-Feinschliff rot, ohne dass etwas kaputt wäre.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from app.application.ai_decision_engine import AiDecisionEngine
from app.domain.trade import TradeAction, TradeDecision
from app.infrastructure.llm.anthropic_client import (
    AnthropicClient,
    LlmVerificationError,
)

from tests.eval.scenarios import SCENARIOS, Scenario

# Drei Läufe je Szenario — Kompromiss aus Aussagekraft und Kosten (§9 des Plans).
RUNS_PER_SCENARIO = 3

pytestmark = pytest.mark.eval


class _DeterministicLlm:
    """Legt `temperature=0` auf jeden Decision-Call.

    Der Produktivpfad bleibt beim API-Default — nur die Eval fährt
    deterministisch, damit ein roter Lauf eine Prompt-Regression bedeutet und
    nicht Sampling-Rauschen.
    """

    def __init__(self, inner: AnthropicClient) -> None:
        self._inner = inner

    async def submit_decision(self, **kwargs: Any) -> dict[str, Any]:
        return await self._inner.submit_decision(temperature=0.0, **kwargs)


# `AiDecisionEngine` fängt jeden LLM-Fehler ab und liefert ein HOLD mit diesem
# Präfix. Für die Produktion ist das richtig — ein Tick darf nicht crashen. Für
# die Eval ist es fatal: ein Szenario, das HOLD erlaubt, wäre grün, obwohl nie
# ein Modell gefragt wurde.
_FALLBACK_MARKER = "AI-Only-Fallback"


@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        pytest.skip("ANTHROPIC_API_KEY fehlt — Eval übersprungen.")

    # Preflight: ohne gültigen Key ist jedes Szenario-Ergebnis wertlos. Ein
    # abgelaufener Key hat in einem echten Lauf 5 von 6 Tests grün gemeldet,
    # weil die Engine still auf HOLD zurückfiel.
    try:
        asyncio.run(AnthropicClient().verify_key(key))
    except LlmVerificationError as exc:
        pytest.fail(
            f"ANTHROPIC_API_KEY ist gesetzt, aber nicht nutzbar: {exc}\n"
            "Die Eval wird abgebrochen — mit ungültigem Key misst sie nichts."
        )
    return key


def _reject_fallbacks(scenario: Scenario, decisions: list[TradeDecision]) -> None:
    """Bricht ab, wenn die Engine gar nicht beim Modell war.

    Muss **vor** jeder inhaltlichen Assertion laufen: sonst wird ein 401 oder
    Timeout als Regelverstoß gemeldet und jemand sucht den Fehler im Prompt.
    """
    fallbacks = [d for d in decisions if _FALLBACK_MARKER in d.reason]
    if fallbacks:
        pytest.fail(
            f"[{scenario.name}] {len(fallbacks)} von {len(decisions)} Läufen kamen nicht beim "
            f"Modell an — kein Prompt-Befund, sondern ein Infrastrukturfehler:\n"
            + "\n".join(f"  - {d.reason}" for d in fallbacks)
        )


@pytest.fixture(scope="module")
def engine(api_key: str) -> AiDecisionEngine:
    return AiDecisionEngine(llm=_DeterministicLlm(AnthropicClient()), api_key=api_key)


def _describe(decisions: list[TradeDecision]) -> str:
    return " | ".join(f"{d.action.value}: {d.reason[:90]}" for d in decisions)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario_respects_the_rule(engine: AiDecisionEngine, scenario: Scenario) -> None:
    decisions = [await engine.decide(scenario.context) for _ in range(RUNS_PER_SCENARIO)]
    _reject_fallbacks(scenario, decisions)
    actions = [d.action for d in decisions]

    forbidden_hits = [a for a in actions if a in scenario.forbidden]
    assert not forbidden_hits, (
        f"[{scenario.name}] {scenario.description}\n"
        f"Regel: {scenario.rule}\n"
        f"Verbotene Aktion gewählt: {[a.value for a in forbidden_hits]}\n"
        f"Alle Läufe: {_describe(decisions)}"
    )

    unexpected = [a for a in actions if a not in scenario.allowed]
    assert not unexpected, (
        f"[{scenario.name}] {scenario.description}\n"
        f"Regel: {scenario.rule}\n"
        f"Aktion außerhalb der erlaubten Menge {[a.value for a in scenario.allowed]}: "
        f"{[a.value for a in unexpected]}\n"
        f"Alle Läufe: {_describe(decisions)}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario_produces_a_usable_decision(
    engine: AiDecisionEngine, scenario: Scenario
) -> None:
    """Formfehler sind teurer als Fehlentscheidungen: eine BUY-Aktion ohne
    Spieler-ID oder Preis wird vom Executor verworfen — der Tick ist verloren."""
    decision = await engine.decide(scenario.context)
    _reject_fallbacks(scenario, [decision])

    assert decision.reason.strip(), "Entscheidung ohne Begründung"
    if decision.action is TradeAction.HOLD:
        return
    assert decision.player_id, f"{decision.action.value} ohne player_id"
    if decision.action in {TradeAction.BUY, TradeAction.LIST_ON_MARKET}:
        assert decision.price is not None and decision.price > 0
