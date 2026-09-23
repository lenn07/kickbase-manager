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
    """Sonst prüft die Eval nebenbei die -100-Regel statt der gemeinten Regel."""
    for scenario in SCENARIOS:
        payload = _build_user_payload(scenario.context)
        assert payload["starting_xi_count"] == 11, (
            f"{scenario.name}: {payload['starting_xi_count']} Spieler aufgestellt"
        )


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
