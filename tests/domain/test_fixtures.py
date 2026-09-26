"""Gegnerstärke und Restspielplan (P2-11).

Der Schwerpunkt liegt auf der **Richtung** der FDR-Skala. Bei `prob` hat
dieselbe Frage einen eigenen Eintrag im Optimizing-Plan gebraucht (§8/F2), weil
eine invertierte Skala Ersatzspieler zu Stammkräften macht; hier wäre die Folge,
dass das Modell die Spieler mit den schwersten Spielen bevorzugt. Ein Test, der
nur „irgendeine Zahl zwischen 1 und 5" prüft, würde das nicht merken.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.fixtures import build_team_outlooks, fixture_difficulty
from app.domain.models import Fixture, TeamStanding

NOW = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)


def _standing(
    team_id: str,
    rank: int,
    *,
    goal_difference: int = 0,
    matches_played: int = 4,
    name: str = "",
) -> TeamStanding:
    return TeamStanding(
        team_id=team_id,
        team_name=name or f"Team {team_id}",
        rank=rank,
        points=max(0, 30 - rank),
        matches_played=matches_played,
        goal_difference=goal_difference,
    )


def _fixture(
    home: str,
    away: str,
    *,
    day: int = 5,
    days_ahead: float = 16,
    finished: bool = False,
) -> Fixture:
    return Fixture(
        matchday=day,
        kickoff=NOW + timedelta(days=days_ahead),
        home_team_id=home,
        away_team_id=away,
        is_finished=finished,
    )


# -- FDR-Skala ------------------------------------------------------------


def test_table_leader_is_the_hardest_opponent() -> None:
    """1 = leicht, 5 = schwer. Die Richtung ist die Hauptfehlerquelle des Pakets."""
    assert fixture_difficulty(_standing("3", 1)) == 5
    assert fixture_difficulty(_standing("15", 18)) == 1


def test_rank_bands_are_monotone() -> None:
    """Ein besserer Tabellenplatz darf nie eine niedrigere Schwierigkeit ergeben."""
    levels = [fixture_difficulty(_standing("x", rank)) for rank in range(1, 19)]
    assert all(level is not None for level in levels)
    # Absteigend (oder gleich) von Platz 1 bis Platz 18.
    assert levels == sorted(levels, reverse=True)
    assert levels[0] == 5
    assert levels[-1] == 1


def test_band_edges_sit_where_the_constant_says() -> None:
    assert fixture_difficulty(_standing("a", 3)) == 5
    assert fixture_difficulty(_standing("a", 4)) == 4
    assert fixture_difficulty(_standing("a", 6)) == 4
    assert fixture_difficulty(_standing("a", 7)) == 3
    assert fixture_difficulty(_standing("a", 12)) == 3
    assert fixture_difficulty(_standing("a", 13)) == 2
    assert fixture_difficulty(_standing("a", 15)) == 2
    assert fixture_difficulty(_standing("a", 16)) == 1


def test_goal_difference_corrects_the_young_table() -> None:
    """Nach vier Spieltagen trennt der Tabellenplatz kaum, die Tordifferenz schon.

    Platz 7 ist Mittelfeld (Stufe 3). Wer dort mit +2 Toren pro Spiel steht, ist
    trotzdem ein schwerer Gegner; wer mit -2 dort steht, ein leichter.
    """
    assert fixture_difficulty(_standing("a", 7, goal_difference=8)) == 4
    assert fixture_difficulty(_standing("a", 7, goal_difference=-8)) == 2
    # Knapp unter der Schwelle (1,25 Tore/Spiel) bleibt es beim Platz.
    assert fixture_difficulty(_standing("a", 7, goal_difference=5)) == 3


def test_scale_never_leaves_its_bounds() -> None:
    """Der Clamp hält 1..5 — sonst käme eine sechste Stufe in den Prompt."""
    assert fixture_difficulty(_standing("a", 1, goal_difference=40)) == 5
    assert fixture_difficulty(_standing("a", 18, goal_difference=-40)) == 1


def test_goal_difference_needs_a_played_match() -> None:
    """Am 1. Spieltag stehen alle bei 0:0 — dann korrigiert nichts."""
    assert fixture_difficulty(_standing("a", 7, goal_difference=0, matches_played=0)) == 3


def test_opponent_without_a_table_row_has_no_difficulty() -> None:
    """`None` statt einer geratenen 3: sonst wäre sie von einer echten nicht zu
    unterscheiden (Plan §9)."""
    assert fixture_difficulty(None) is None


# -- Spielplan ------------------------------------------------------------


def test_outlook_picks_the_next_unplayed_fixture() -> None:
    standings = [_standing("1", 1, name="Spitze"), _standing("2", 18, name="Keller")]
    fixtures = [
        _fixture("1", "2", day=4, days_ahead=-5, finished=True),
        _fixture("2", "1", day=5, days_ahead=16),
        _fixture("1", "2", day=6, days_ahead=23),
    ]
    outlooks = build_team_outlooks(standings, fixtures, now=NOW)

    home_team = outlooks["2"]
    assert home_team.next_opponent_id == "1"
    assert home_team.next_opponent_name == "Spitze"
    assert home_team.next_opponent_rank == 1
    assert home_team.is_home is True
    assert home_team.fdr == 5
    assert home_team.next_matchday == 5

    away_team = outlooks["1"]
    assert away_team.is_home is False
    assert away_team.fdr == 1


def test_a_running_match_is_not_the_next_one() -> None:
    """Ein angepfiffenes Spiel trägt noch `st: 0`, taugt aber nicht als Vorschau.

    Deshalb prüft der Aufbau beides: Status **und** Anpfiffzeit. Ohne die zweite
    Bedingung stünde am Samstagabend das laufende Spiel als „nächstes" im
    Payload, und das Modell würde eine Aufstellung dafür planen.
    """
    fixtures = [
        _fixture("1", "2", day=5, days_ahead=-0.05),
        _fixture("2", "1", day=6, days_ahead=7),
    ]
    outlooks = build_team_outlooks([], fixtures, now=NOW)
    assert outlooks["1"].next_matchday == 6
    assert outlooks["1"].is_home is False


def test_fdr_next3_averages_the_rest_schedule() -> None:
    """Der Restspielplan: drei Spitzenteams sind eine andere Wette als drei Kellerkinder."""
    standings = [
        _standing("1", 1),
        _standing("2", 2),
        _standing("3", 3),
        _standing("9", 9),
    ]
    fixtures = [
        _fixture("9", "1", day=5, days_ahead=1),
        _fixture("2", "9", day=6, days_ahead=8),
        _fixture("9", "3", day=7, days_ahead=15),
        # Das vierte Spiel darf den Mittelwert nicht mehr beeinflussen.
        _fixture("9", "1", day=8, days_ahead=22),
    ]
    outlooks = build_team_outlooks(standings, fixtures, now=NOW)
    assert outlooks["9"].fdr == 5
    assert outlooks["9"].fdr_next3 == 5.0

    with_easy = build_team_outlooks(
        [_standing("9", 9), _standing("1", 17), _standing("2", 18), _standing("3", 16)],
        fixtures,
        now=NOW,
    )
    assert with_easy["9"].fdr_next3 == 1.0


def test_team_without_upcoming_fixture_is_unknown_not_neutral() -> None:
    """Saisonende: kein nächstes Spiel. Alles `None`, `is_known` False."""
    outlooks = build_team_outlooks(
        [_standing("1", 1)],
        [_fixture("1", "2", day=34, days_ahead=-3, finished=True)],
        now=NOW,
    )
    assert outlooks["1"].is_known is False
    assert outlooks["1"].fdr is None
    assert outlooks["1"].next_opponent_id is None


def test_fixtures_carry_the_team_set_even_without_a_table() -> None:
    """Fällt die Tabelle aus, bleiben Gegner und Heimrecht bekannt.

    Das ist mehr als nichts: „auswärts bei Team 2" trägt eine Aussage, auch ohne
    dass die Stärke bekannt ist. Die Schwierigkeit bleibt `None` statt geraten.
    """
    outlooks = build_team_outlooks([], [_fixture("1", "2")], now=NOW)
    assert set(outlooks) == {"1", "2"}
    assert outlooks["1"].next_opponent_id == "2"
    assert outlooks["1"].next_opponent_name is None
    assert outlooks["1"].fdr is None
    assert outlooks["1"].fdr_next3 is None
    assert outlooks["1"].is_known is True
