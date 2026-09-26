"""Prompt-Eval: echte Modell-Calls gegen feste Szenarien (Testgerüst Stufe 4).

**Kostet Geld.** Läuft nicht im Default-Run — `addopts` filtert `-m "not eval"`.
Vor jedem Merge an `docs/master_prompt.md` einmal ausführen:

    ANTHROPIC_API_KEY=sk-... pytest -m eval -s

`-s` zeigt je Szenario die gewählten Aktionen und die erste Begründung — bei
einem Prompt-Merge ist das der eigentliche Befund, nicht das grüne Häkchen.

⚠️ **Nicht durch `| tail` oder `| head` schicken.** Der Exit-Code der Pipe ist
dann der des letzten Glieds, nicht der von pytest: ein Lauf mit roten Tests
meldet `0` und sieht bestanden aus. Und der abgeschnittene Teil enthält genau
die Begründungen, für die man bezahlt hat. Wenn die Ausgabe zu lang ist:
`pytest -m eval -s > eval.log 2>&1; echo $?` und die Datei danach lesen.

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


# `AiDecisionEngine` fängt jeden Fehler ab und liefert ein HOLD mit diesem
# Präfix. Für die Produktion ist das richtig — ein Tick darf nicht crashen. Für
# die Eval ist es fatal: ein Szenario, das HOLD erlaubt, wäre grün, obwohl nie
# ein Modell gefragt wurde.
_FALLBACK_MARKER = "AI-Only-Fallback"
# Die beiden Fälle dahinter bedeuten **Gegensätzliches** und dürfen nicht
# denselben Fehlertext bekommen:
_TRANSPORT_MARKER = "AI-Only-Fallback (LLM-Fehler)"  # Timeout, 401 — Infrastruktur
_REJECTED_MARKER = "AI-Only-Fallback (ungültige Antwort)"  # Code-Sperre — Prompt-Befund


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
    """Bricht ab, wenn die Engine die Antwort nicht vom Modell hat.

    Muss **vor** jeder inhaltlichen Assertion laufen: sonst wird ein 401 oder
    Timeout als Regelverstoß gemeldet und jemand sucht den Fehler im Prompt.

    Seit P2-14 gibt es zwei Gründe für ein Fallback-HOLD, und sie bedeuten das
    **Gegenteil** voneinander:

    - **Transport** (Timeout, 401): das Modell wurde nie gefragt. Kein
      Prompt-Befund, der Lauf ist an dieser Stelle wertlos.
    - **Zurückgewiesen**: das Modell hat geantwortet, und eine Code-Sperre hat
      die Antwort verworfen — ein Nachgebot ohne Wirkung (D3) oder ein Kauf
      ohne Kaderplatz. Das ist ein **echter Regelverstoß**, nur einer, den der
      Betrieb abfängt. Ohne diese Unterscheidung würde er künftig als
      „Infrastrukturfehler" durchgehen und niemand sähe ihn sich an.
    """
    rejected = [d for d in decisions if _REJECTED_MARKER in d.reason]
    if rejected:
        pytest.fail(
            f"[{scenario.name}] {len(rejected)} von {len(decisions)} Antworten wurden von einer "
            f"**Code-Sperre** verworfen. Das Modell hat geantwortet und dabei eine Regel "
            f"verletzt, die der Code auffängt — ein Prompt-Befund, kein Transportproblem:\n"
            f"Regel: {scenario.rule}\n" + "\n".join(f"  - {d.reason}" for d in rejected)
        )

    transport = [d for d in decisions if _TRANSPORT_MARKER in d.reason]
    if transport:
        pytest.fail(
            f"[{scenario.name}] {len(transport)} von {len(decisions)} Läufen kamen nicht beim "
            f"Modell an — kein Prompt-Befund, sondern ein Infrastrukturfehler:\n"
            + "\n".join(f"  - {d.reason}" for d in transport)
        )

    other = [
        d
        for d in decisions
        if _FALLBACK_MARKER in d.reason and d not in rejected and d not in transport
    ]
    if other:
        pytest.fail(
            f"[{scenario.name}] Unbekannter Fallback-Grund — der Marker in "
            f"`ai_decision_engine.py` hat sich geändert, ohne dass dieser Test nachgezogen "
            f"wurde:\n" + "\n".join(f"  - {d.reason}" for d in other)
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

    Seit P2-13 steht die **Gebotshöhe** mit dabei. Der ganze §3 dreht sich um
    den Aufschlag über Marktwert; ein Lauf, der nur „BUY x3" meldet, lässt genau
    die Zahl weg, um die es geht — und er verbirgt, ob eine
    `min_bid_ratio`/`max_bid_ratio`-Schranke überhaupt geprüft wurde. Wählt das
    Modell `HOLD`, wird keine der beiden ausgeübt: die Zeile unten macht das
    sichtbar, statt es als grünes Häkchen zu tarnen.
    """
    counts = Counter(d.action.value for d in decisions)
    verteilung = ", ".join(f"{action} x{n}" for action, n in counts.most_common())
    print(f"\n  [{scenario.name}] {verteilung}")
    print(f"    Regel : {scenario.rule}")
    print(f"    Gebot : {_describe_bids(scenario, decisions)}")
    print(f"    Grund : {decisions[0].reason[:220]}")


def _describe_bids(scenario: Scenario, decisions: list[TradeDecision]) -> str:
    """Gebote als Aufschlag über Marktwert — plus der Hinweis, wenn keins fiel."""
    market_values = {mp.player.id: mp.player.market_value for mp in scenario.context.market}
    parts: list[str] = []
    for decision in decisions:
        if decision.action is not TradeAction.BUY or decision.price is None:
            continue
        market_value = market_values.get(decision.player_id or "")
        if market_value is None or market_value <= 0:
            parts.append(f"{int(decision.price):,}")
            continue
        ratio = float(decision.price) / float(market_value)
        parts.append(f"{int(decision.price):,} ({ratio - 1:+.1%} auf MW)")
    if parts:
        return " · ".join(parts)
    schranken = [
        name
        for name, value in (
            ("min_bid_ratio", scenario.min_bid_ratio),
            ("max_bid_ratio", scenario.max_bid_ratio),
        )
        if value is not None
    ]
    if schranken:
        return f"kein BUY — {'/'.join(schranken)} in diesem Lauf NICHT geprüft"
    return "kein BUY"


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

    _assert_bid_is_in_range(scenario, decisions)
    _assert_chain_is_long_enough(scenario, decisions)


def _assert_chain_is_long_enough(scenario: Scenario, decisions: list[TradeDecision]) -> None:
    """Prüft, dass eine Lage mit zwei Problemen auch zwei Aktionen bekommt (P2-16).

    Ohne diese Prüfung bliebe `follow_up_actions` unbelegt: ein Szenario, in
    dem nur eine von zwei Regelverletzungen behoben wird, sähe grün aus,
    solange die gewählte Aktion erlaubt ist.
    """
    if scenario.min_chain_length <= 1:
        return
    for decision in decisions:
        assert len(decision.chain) >= scenario.min_chain_length, (
            f"[{scenario.name}] {scenario.description}\n"
            f"Regel: {scenario.rule}\n"
            f"Nur {len(decision.chain)} Aktion(en): "
            f"{[step.action.value for step in decision.chain]} — erwartet sind mindestens "
            f"{scenario.min_chain_length}. Im Deadline-Fenster bleibt sonst die Hälfte der "
            "Lage ungelöst, und ein zweiter Tick kommt nicht mehr.\n"
            f"Alle Läufe: {_describe(decisions)}"
        )


def _assert_bid_is_in_range(scenario: Scenario, decisions: list[TradeDecision]) -> None:
    """Prüft die Gebotshöhe, wo eine Regel sie vorschreibt — nach unten und oben.

    Beim Underpay-Block ist weder die Aktion noch die Auswahl falsch, sondern
    allein der Preis: ein BUY zu 92 % des Marktwerts wird von Kickbase gar nicht
    erst angenommen und verbrennt den Tick. Dasselbe gilt seit P2-13 in der
    anderen Richtung — ein Overbid ohne Begründung verbrennt kein Tick, sondern
    Geld, und zwar jedes Mal. Nur eine Grenze zu prüfen würde ein Modell
    belohnen, das im Zweifel immer zu viel bietet.
    """
    if scenario.min_bid_ratio is None and scenario.max_bid_ratio is None:
        return
    market_values = {mp.player.id: mp.player.market_value for mp in scenario.context.market}
    for decision in decisions:
        if decision.action is not TradeAction.BUY or decision.price is None:
            continue
        market_value = market_values.get(decision.player_id or "")
        if market_value is None or market_value <= 0:
            continue
        ratio = float(decision.price) / float(market_value)
        if scenario.min_bid_ratio is not None:
            assert ratio >= scenario.min_bid_ratio, (
                f"[{scenario.name}] {scenario.description}\n"
                f"Regel: {scenario.rule}\n"
                f"Gebot {int(decision.price):,} liegt bei {ratio:.1%} des Marktwerts "
                f"{int(market_value):,} — verlangt sind mindestens "
                f"{scenario.min_bid_ratio:.0%}.\n"
                f"Alle Läufe: {_describe(decisions)}"
            )
        if scenario.max_bid_ratio is not None:
            assert ratio <= scenario.max_bid_ratio, (
                f"[{scenario.name}] {scenario.description}\n"
                f"Regel: {scenario.rule}\n"
                f"Gebot {int(decision.price):,} liegt bei {ratio:.1%} des Marktwerts "
                f"{int(market_value):,} — erlaubt sind höchstens "
                f"{scenario.max_bid_ratio:.0%}. Ein Aufschlag ohne Drift- oder "
                "Konkurrenz-Begründung ist verschenktes Geld.\n"
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
