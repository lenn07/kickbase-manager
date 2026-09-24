"""Prüft die Eval-Szenarien **ohne** Modell-Call.

Ohne diesen Test fällt ein kaputtes Szenario erst im bezahlten `-m eval`-Lauf
auf — nach dem Modell-Call, nicht davor. Deshalb läuft er im Default-Run mit.
"""

from __future__ import annotations

from app.application.ai_decision_engine import _build_user_payload
from app.domain.trade import TradeAction

from tests.eval.scenarios import SCENARIOS


def test_every_scenario_builds_a_valid_payload() -> None:
    for scenario in SCENARIOS:
        payload = _build_user_payload(scenario.context)
        assert payload["squad"], f"{scenario.name}: leerer Kader"
        assert payload["market"], f"{scenario.name}: leerer Markt"
        assert payload["budget"]["team_value"] > 0, f"{scenario.name}: kein Teamwert"
        assert payload["budget"]["max_negative_allowed"] < 0, f"{scenario.name}: 33 %-Regel fehlt"
        assert payload["minutes_until_matchday_start"] is not None


def test_scenarios_have_eleven_players_in_the_starting_xi() -> None:
    """Sonst prüft die Eval nebenbei die -100-Regel statt der gemeinten Regel.

    Ausnahme: Szenarien, in denen die Aufstellung *der Gegenstand* ist. Die
    müssen das über `expects_full_lineup=False` erklären — und dann auch
    `SET_LINEUP` erlauben, **sofern es überhaupt hilft**.

    Es hilft nur, wenn mehr Spieler im Kader stehen als aufgestellt sind; sonst
    gibt es niemanden, den man nachrücken könnte. Bei einem Kader **unter** elf
    Spielern ist jede Aufstellung bereits maximal besetzt, und `SET_LINEUP`
    wäre ein verbrauchter Tick, der keinen einzigen Slot schließt — dort ist
    der Kauf die einzige Antwort. Diese Unterscheidung stand bis 2026-09-24
    nicht im Wächter, weil es noch kein Szenario mit zu kleinem Kader gab.
    """
    for scenario in SCENARIOS:
        payload = _build_user_payload(scenario.context)
        if not scenario.expects_full_lineup:
            assert payload["lineup"]["empty_slots"] > 0, (
                f"{scenario.name}: als unvollständig deklariert, ist es aber nicht"
            )
            bench = payload["squad_size"] - payload["starting_xi_count"]
            if bench > 0:
                assert TradeAction.SET_LINEUP in scenario.allowed, (
                    f"{scenario.name}: {bench} Spieler auf der Bank, aber SET_LINEUP "
                    "nicht erlaubt — dann hat das Szenario keine gültige Antwort"
                )
            continue
        assert payload["starting_xi_count"] == 11, (
            f"{scenario.name}: {payload['starting_xi_count']} Spieler aufgestellt"
        )
        assert payload["lineup"]["empty_slots"] == 0, (
            f"{scenario.name}: {payload['lineup']['empty_slots']} leere Slots im lineup-Block"
        )


def test_every_scenario_carries_a_usable_lineup_block() -> None:
    """Ohne Formation kann das Modell keine gültige `SET_LINEUP`-Aktion bauen."""
    for scenario in SCENARIOS:
        block = _build_user_payload(scenario.context)["lineup"]
        assert block["formation"] in block["allowed_formations"], scenario.name
        assert block["deadline_iso"], scenario.name


def test_allowed_and_forbidden_do_not_overlap() -> None:
    for scenario in SCENARIOS:
        overlap = scenario.allowed & scenario.forbidden
        assert not overlap, f"{scenario.name}: {overlap} ist gleichzeitig erlaubt und verboten"
        assert scenario.allowed, f"{scenario.name}: keine erlaubte Aktion definiert"
        assert scenario.rule, f"{scenario.name}: keine Regel benannt"


def test_every_scenario_leaves_a_way_out() -> None:
    """Ein Szenario, das jede Aktion verbietet, kann das Modell nur verlieren."""
    for scenario in SCENARIOS:
        assert scenario.allowed - scenario.forbidden, f"{scenario.name}: keine zulässige Antwort"
        assert TradeAction.HOLD in scenario.allowed or scenario.forbidden, (
            f"{scenario.name}: HOLD verboten, aber keine Begründung über `forbidden`"
        )
