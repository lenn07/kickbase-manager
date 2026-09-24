"""Prompt-Eval: echte Modell-Calls gegen feste Szenarien (Testgerüst Stufe 4).

**Kostet Geld.** Läuft nicht im Default-Run — `addopts` filtert `-m "not eval"`.
Vor jedem Merge an `docs/master_prompt.md` einmal ausführen:

    ANTHROPIC_API_KEY=sk-... pytest -m eval -s

`-s` zeigt je Szenario die gewählten Aktionen und die erste Begründung — bei
einem Prompt-Merge ist das der eigentliche Befund, nicht das grüne Häkchen.

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
from collections import Counter

import pytest
from app.application.ai_decision_engine import AiDecisionConfig, AiDecisionEngine
from app.domain.trade import TradeAction, TradeDecision
from app.infrastructure.llm.anthropic_client import (
    AnthropicClient,
    LlmVerificationError,
)

from tests.eval.scenarios import SCENARIOS, Scenario

# Drei Läufe je Szenario — Kompromiss aus Aussagekraft und Kosten (§9 des Plans).
RUNS_PER_SCENARIO = 3

pytestmark = pytest.mark.eval


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
    """Die Eval fährt den **Produktivpfad**, ohne Sonderkonfiguration.

    Bis P0-5 lag hier ein Wrapper, der `temperature=0` aufsetzte, weil der
    Produktivpfad am API-Default lief. Das war doppelt falsch: der Bot traf
    seine echten Entscheidungen weiter mit Sampling, und die Eval hätte den
    Verlust der Einstellung nie gemeldet — sie stellte sie ja selbst her.
    Jetzt steht `temperature=0` in `AiDecisionConfig`, und der Test unten
    bewacht sie.
    """
    return AiDecisionEngine(llm=AnthropicClient(), api_key=api_key)


def test_eval_measures_a_deterministic_path() -> None:
    """Ohne `temperature=0` messen drei Läufe je Szenario Sampling, nicht Treue.

    Läuft vor jedem bezahlten Szenario — ein Lauf, der nur Rauschen misst, ist
    das Geld nicht wert (Plan §9, „Prompt-Regression durch Sampling").
    """
    assert AiDecisionConfig().temperature == 0.0


def _describe(decisions: list[TradeDecision]) -> str:
    return " | ".join(f"{d.action.value}: {d.reason[:90]}" for d in decisions)


def _report(scenario: Scenario, decisions: list[TradeDecision]) -> None:
    """Zeigt die gewählten Aktionen auch bei grünem Lauf (`pytest -m eval -s`).

    Ein Lauf kostet echte Calls; nur „passed" zu melden verschenkt genau die
    Information, für die man bezahlt hat — vor allem bei einem Prompt-Merge,
    wo man den Effekt der Änderung sehen will, nicht nur ihre Zulässigkeit.
    """
    counts = Counter(d.action.value for d in decisions)
    verteilung = ", ".join(f"{action} x{n}" for action, n in counts.most_common())
    print(f"\n  [{scenario.name}] {verteilung}")
    print(f"    Regel : {scenario.rule}")
    print(f"    Grund : {decisions[0].reason[:220]}")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario_respects_the_rule(engine: AiDecisionEngine, scenario: Scenario) -> None:
    decisions = [await engine.decide(scenario.context) for _ in range(RUNS_PER_SCENARIO)]
    _reject_fallbacks(scenario, decisions)
    _report(scenario, decisions)
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

    # Manche Regeln verbieten keine Aktionsart, sondern eine Auswahl: „kaufen
    # ist in Ordnung, **den** zu kaufen nicht". Ohne diese Prüfung liesse sich
    # so ein Szenario nur als Verbot der ganzen Aktion formulieren — und das
    # misst dann eine andere Regel als die gemeinte.
    picked_forbidden = [
        d.player_id
        for d in decisions
        if d.player_id is not None and d.player_id in scenario.forbidden_player_ids
    ]
    assert not picked_forbidden, (
        f"[{scenario.name}] {scenario.description}\n"
        f"Regel: {scenario.rule}\n"
        f"Verbotener Spieler gewählt: {picked_forbidden}\n"
        f"Alle Läufe: {_describe(decisions)}"
    )

    _assert_bid_is_high_enough(scenario, decisions)


def _assert_bid_is_high_enough(scenario: Scenario, decisions: list[TradeDecision]) -> None:
    """Prüft die Gebotshöhe, wo eine Regel sie vorschreibt.

    Beim Underpay-Block ist weder die Aktion noch die Auswahl falsch, sondern
    allein der Preis — ein BUY zu 92 % des Marktwerts wird von Kickbase gar
    nicht erst angenommen und verbrennt den Tick.
    """
    if scenario.min_bid_ratio is None:
        return
    market_values = {mp.player.id: mp.player.market_value for mp in scenario.context.market}
    for decision in decisions:
        if decision.action is not TradeAction.BUY or decision.price is None:
            continue
        market_value = market_values.get(decision.player_id or "")
        if market_value is None or market_value <= 0:
            continue
        ratio = float(decision.price) / float(market_value)
        assert ratio >= scenario.min_bid_ratio, (
            f"[{scenario.name}] {scenario.description}\n"
            f"Regel: {scenario.rule}\n"
            f"Gebot {int(decision.price):,} liegt bei {ratio:.1%} des Marktwerts "
            f"{int(market_value):,} — verlangt sind mindestens "
            f"{scenario.min_bid_ratio:.0%}.\n"
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
