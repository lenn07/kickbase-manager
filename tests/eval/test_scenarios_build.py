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


def test_every_scenario_knows_its_next_opponent() -> None:
    """Seit P2-11 gehört der Spielplan zur Lage — sonst misst die Eval den Vorzustand.

    Ohne Gegner im Payload steht an jedem Spieler `missing_data:fixtures`, und
    §1.2 verlangt dann ausdrücklich, ohne Spielplan zu entscheiden. Die Eval
    würde also weiter den Zustand vor P2-11 prüfen, während der Bot produktiv
    mit Gegnerstärke arbeitet.
    """
    for scenario in SCENARIOS:
        payload = _build_user_payload(scenario.context)
        for entry in payload["squad"] + payload["market"]:
            assert entry["fdr"] is not None, (
                f"{scenario.name}: {entry['name']} ohne Gegnerstärke — "
                "fehlt ein Outlook für dessen `team_id`?"
            )
            assert 1 <= entry["fdr"] <= 5, f"{scenario.name}: fdr {entry['fdr']} außerhalb 1..5"
            assert "missing_data:fixtures" not in entry["missing_data_flags"], scenario.name


# Das eine Szenario, dessen **Gegenstand** ein Listing ohne Marktwert-Update ist.
_NO_MV_UPDATE_SCENARIO = "no_trade_without_a_mv_update"


def test_only_one_scenario_lacks_a_market_value_update() -> None:
    """`mv_updates_until_expiry == 0` ist seit §3a eine harte Kaufbremse.

    Der Befund aus dem bezahlten Lauf vom 2026-09-26: die Defaults von
    `_context` (Listing 6 h, nächstes Update in 8 h) ließen **jedes**
    Default-Listing vor dem Update ablaufen. Sechs Szenarien endeten daraufhin
    einstimmig auf `HOLD` mit der Begründung „kein Trade-Gewinn möglich" — und
    `underpay_is_blocked` meldete grün für eine Gebots-Regel, die es nie geprüft
    hat, weil eine Preisschranke nur bei `BUY` greift.

    Der Default darf deshalb nicht mit der ausdrücklich gemeinten Ausnahme
    zusammenfallen. Dieselbe Klasse von Befund wie `mv_max_30d == market_value`
    in P2-13: eine Voreinstellung, die nebenbei jede Lage zu einer
    Sonderlage macht, kostet nicht nur einen bezahlten Lauf — sie tarnt sich als
    grünes Häkchen.
    """
    for scenario in SCENARIOS:
        market = _build_user_payload(scenario.context)["market"]
        updates = [entry["mv_updates_until_expiry"] for entry in market]
        if scenario.name == _NO_MV_UPDATE_SCENARIO:
            assert all(value == 0 for value in updates), (
                f"{scenario.name} ist die Ausnahme und muss bei 0 bleiben: {updates}"
            )
            continue
        assert any(value is None or value >= 1 for value in updates), (
            f"{scenario.name}: kein Listing überlebt ein Marktwert-Update "
            f"({updates}) — §3a verbietet dann jeden Trade-Kauf, und das Szenario "
            "prüft seine eigene Regel nicht mehr."
        )


def test_selection_rules_force_a_choice() -> None:
    """Eine Regel über die **Auswahl** braucht ein Szenario, in dem gewählt wird.

    `forbidden_player_ids` greift nur, wenn das Modell überhaupt eine
    `player_id` liefert. Ist `HOLD` erlaubt, kann ein Szenario grün melden, ohne
    die Regel berührt zu haben — genau so ist `easier_fixture_wins_the_duel` im
    ersten bezahlten Lauf durchgelaufen (3x HOLD).

    Deshalb: wer eine Auswahl verbietet, muss entweder `HOLD` ausschließen oder
    mindestens zwei Kandidaten anbieten, zwischen denen die Regel entscheidet.
    Die zweite Variante ist schwächer und bleibt erlaubt — sie deckt Lagen ab,
    in denen Zurückhaltung selbst vertretbar ist (`bid_already_running`,
    `joker_is_no_starter`).
    """
    for scenario in SCENARIOS:
        if not scenario.forbidden_player_ids:
            continue
        market = _build_user_payload(scenario.context)["market"]
        forces_action = TradeAction.HOLD not in scenario.allowed
        assert forces_action or len(market) > 1, (
            f"{scenario.name}: verbietet eine Auswahl, erlaubt HOLD und bietet nur "
            f"{len(market)} Marktspieler — die Regel kann nicht geprüft werden."
        )
