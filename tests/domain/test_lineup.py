"""Aufstellungs-Regeln (P0-4) — die teuerste Regel des Spiels.

Jeder leere Startelf-Slot kostet 100 Punkte. Die Tests hier prüfen deshalb
zwei Dinge besonders genau: dass nichts Ungültiges durchkommt (eine abgelehnte
Aufstellung kostet Punkte, die kein späterer Tick zurückholt), und dass bei
einem **unvollständigen** Kader trotzdem so viel besetzt wird wie möglich.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.lineup import (
    FORMATIONS,
    LINEUP_SIZE,
    Lineup,
    best_lineup,
    slots_for,
    validate_lineup,
)
from app.domain.models import Player, PlayerStatus, Position, SquadPlayer


def _sp(
    pid: str,
    position: Position,
    *,
    average_points: float | None = 100.0,
    status: PlayerStatus = PlayerStatus.FIT,
) -> SquadPlayer:
    return SquadPlayer(
        player=Player(
            id=pid,
            first_name="",
            last_name=pid,
            team_id="2",
            position=position,
            status=status,
            market_value=Decimal(5_000_000),
            average_points=average_points,
        )
    )


def _full_squad() -> list[SquadPlayer]:
    """Kader für 3-5-2 plus Bank."""
    return [
        _sp("gk1", Position.GOALKEEPER, average_points=110.0),
        _sp("gk2", Position.GOALKEEPER, average_points=40.0),
        *[_sp(f"def{i}", Position.DEFENDER, average_points=100.0 - i) for i in range(1, 6)],
        *[_sp(f"mid{i}", Position.MIDFIELDER, average_points=120.0 - i) for i in range(1, 7)],
        *[_sp(f"fwd{i}", Position.FORWARD, average_points=130.0 - i) for i in range(1, 4)],
    ]


# -- Formationen ----------------------------------------------------------


def test_every_formation_adds_up_to_eleven() -> None:
    """Zehn Feldspieler plus genau ein Torwart — sonst ist die Tabelle falsch."""
    for formation in FORMATIONS:
        slots = slots_for(formation)
        assert slots is not None
        assert sum(slots.values()) == LINEUP_SIZE, formation
        assert slots[Position.GOALKEEPER] == 1, formation


def test_formation_names_match_their_slot_counts() -> None:
    """Der Name ist die Dokumentation — ein Tippfehler darin wäre unsichtbar."""
    for formation, counts in FORMATIONS.items():
        assert tuple(int(part) for part in formation.split("-")) == counts


def test_unknown_formation_has_no_slots() -> None:
    assert slots_for("6-6-6") is None


# -- validate_lineup ------------------------------------------------------


def test_valid_full_lineup_passes() -> None:
    squad = _full_squad()
    ids = ["gk1", "def1", "def2", "def3", "mid1", "mid2", "mid3", "mid4", "mid5", "fwd1", "fwd2"]
    assert validate_lineup(ids, "3-5-2", squad) == []


def test_partial_lineup_is_allowed() -> None:
    """Ein Kader unter elf Spielern muss trotzdem aufstellen dürfen.

    Am 2026-09-23 hatte der echte Kader acht Spieler. Eine Regel, die „genau
    elf" verlangt, macht `SET_LINEUP` genau dann unmöglich, wenn jeder besetzte
    Slot 100 Punkte wert ist.
    """
    squad = _full_squad()
    assert validate_lineup(["gk1", "def1", "mid1"], "3-5-2", squad) == []


def test_too_many_players_is_rejected() -> None:
    squad = _full_squad()
    ids = [sp.player.id for sp in squad][:12]
    errors = validate_lineup(ids, "3-5-2", squad)
    assert any("höchstens" in e for e in errors)


def test_duplicate_player_is_rejected() -> None:
    squad = _full_squad()
    errors = validate_lineup(["gk1", "def1", "def1"], "3-5-2", squad)
    assert any("mehrfach" in e for e in errors)


def test_foreign_player_is_rejected() -> None:
    """Eine ID, die nicht im Kader steht, ist entweder ein Tippfehler oder
    halluziniert — in beiden Fällen ein verlorener Tick."""
    squad = _full_squad()
    errors = validate_lineup(["gk1", "fremder"], "3-5-2", squad)
    assert any("Nicht im Kader" in e for e in errors)


def test_overfilled_position_is_rejected() -> None:
    """Vier Verteidiger in einem 3-5-2 würde Kickbase ablehnen."""
    squad = _full_squad()
    errors = validate_lineup(["def1", "def2", "def3", "def4"], "3-5-2", squad)
    assert any("DEFENDER" in e for e in errors)


def test_second_goalkeeper_is_rejected() -> None:
    squad = _full_squad()
    errors = validate_lineup(["gk1", "gk2"], "3-5-2", squad)
    assert any("GOALKEEPER" in e for e in errors)


def test_unknown_formation_is_rejected() -> None:
    squad = _full_squad()
    errors = validate_lineup(["gk1"], "9-9-9", squad)
    assert any("Unbekannte Formation" in e for e in errors)


def test_all_violations_are_reported_at_once() -> None:
    """Wer eine kaputte Aufstellung debuggt, will nicht zehnmal je einen Fehler."""
    squad = _full_squad()
    errors = validate_lineup(["fremder", "def1", "def1"], "9-9-9", squad)
    assert len(errors) >= 3


def test_empty_lineup_is_formally_valid() -> None:
    """Leer ist nicht ungültig, nur nutzlos — die Bewertung macht der Guard."""
    assert validate_lineup([], "3-5-2", _full_squad()) == []


# -- best_lineup ----------------------------------------------------------


def test_best_lineup_fills_all_eleven_slots_from_a_full_squad() -> None:
    lineup = best_lineup(_full_squad(), formation_candidates=("3-5-2",))
    assert lineup.formation == "3-5-2"
    assert len(lineup.player_ids) == LINEUP_SIZE
    assert lineup.is_complete
    assert validate_lineup(lineup.player_ids, lineup.formation, _full_squad()) == []


def test_best_lineup_prefers_the_stronger_player_per_position() -> None:
    squad = [
        _sp("gk1", Position.GOALKEEPER, average_points=110.0),
        _sp("gk2", Position.GOALKEEPER, average_points=10.0),
    ]
    lineup = best_lineup(squad, formation_candidates=("3-5-2",))
    assert lineup.player_ids == ("gk1",)


def test_start_probability_outweighs_raw_points() -> None:
    """Ein Spieler, der nicht aufläuft, bringt 0 — die Chance ist ein Faktor.

    Der 200-Punkte-Mann mit 5 % Startelf-Chance ist im Erwartungswert
    schlechter als der 90-Punkte-Mann, der sicher spielt.
    """
    squad = [
        _sp("star", Position.GOALKEEPER, average_points=200.0),
        _sp("solide", Position.GOALKEEPER, average_points=90.0),
    ]
    lineup = best_lineup(
        squad,
        start_probabilities={"star": 0.05, "solide": 0.95},
        formation_candidates=("3-5-2",),
    )
    assert lineup.player_ids == ("solide",)


def test_missing_probability_is_weighted_neutrally() -> None:
    """Ohne Prognose weder als Stammspieler noch als Reservist behandeln."""
    squad = [
        _sp("unbekannt", Position.GOALKEEPER, average_points=100.0),
        _sp("bekannt", Position.GOALKEEPER, average_points=100.0),
    ]
    lineup = best_lineup(
        squad,
        start_probabilities={"unbekannt": None, "bekannt": 0.9},
        formation_candidates=("3-5-2",),
    )
    assert lineup.player_ids == ("bekannt",)


def test_player_without_points_still_beats_an_empty_slot() -> None:
    """Ein Neuzugang ohne Historie ist 100 Punkte besser als eine Lücke."""
    squad = [_sp("neu", Position.GOALKEEPER, average_points=None)]
    lineup = best_lineup(squad, formation_candidates=("3-5-2",))
    assert lineup.player_ids == ("neu",)


def test_best_lineup_fills_what_it_can_from_an_incomplete_squad() -> None:
    """Der reale Fall vom 2026-09-23: acht Spieler, elf Slots.

    Acht besetzte Slots heissen 300 verschenkte Punkte statt 1100.
    """
    squad = [
        _sp("gk1", Position.GOALKEEPER),
        _sp("def1", Position.DEFENDER),
        _sp("def2", Position.DEFENDER),
        _sp("mid1", Position.MIDFIELDER),
        _sp("mid2", Position.MIDFIELDER),
        _sp("mid3", Position.MIDFIELDER),
        _sp("fwd1", Position.FORWARD),
        _sp("fwd2", Position.FORWARD),
    ]
    lineup = best_lineup(squad, formation_candidates=("3-5-2",))
    assert len(lineup.player_ids) == len(squad)
    assert not lineup.is_complete
    assert validate_lineup(lineup.player_ids, lineup.formation, squad) == []


def test_best_lineup_switches_formation_to_place_more_players() -> None:
    """Eine Formation, die mehr Slots besetzt bekommt, gewinnt.

    Bei drei Stürmern und zwei Verteidigern lässt 3-5-2 einen Stürmer draussen,
    3-4-3 nicht.
    """
    squad = [
        _sp("gk1", Position.GOALKEEPER),
        _sp("def1", Position.DEFENDER),
        _sp("def2", Position.DEFENDER),
        _sp("fwd1", Position.FORWARD),
        _sp("fwd2", Position.FORWARD),
        _sp("fwd3", Position.FORWARD),
    ]
    lineup = best_lineup(squad, formation_candidates=("3-5-2", "3-4-3"))
    assert len(lineup.player_ids) == 6
    assert lineup.formation == "3-4-3"


def test_best_lineup_keeps_the_current_formation_on_a_tie() -> None:
    """Formationswechsel ist zusätzliches Risiko — nur wenn er etwas bringt.

    Verifiziert gegen die API ist bislang allein `3-5-2`; alle anderen Systeme
    stammen aus der Doku.
    """
    squad = _full_squad()
    lineup = best_lineup(squad, formation_candidates=("3-5-2", "4-4-2"))
    assert lineup.formation == "3-5-2"


def test_best_lineup_never_produces_an_invalid_result() -> None:
    """Über alle Formationen hinweg: was rauskommt, muss die Validierung bestehen."""
    squad = _full_squad()
    for formation in FORMATIONS:
        lineup = best_lineup(squad, formation_candidates=(formation,))
        assert validate_lineup(lineup.player_ids, lineup.formation, squad) == [], formation


def test_lineup_is_complete_only_at_eleven() -> None:
    assert not Lineup(formation="3-5-2", player_ids=("a",) * 10).is_complete
    assert Lineup(formation="3-5-2", player_ids=tuple(str(i) for i in range(11))).is_complete
