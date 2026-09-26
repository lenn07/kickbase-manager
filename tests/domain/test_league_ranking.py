"""Ligatabelle: Rang, Rückstand, Saison-Uhr (P2-12).

Die Zahlen hier entscheiden über den Risikoappetit des Prompts — und der
Fehler, den sie verhindern müssen, ist kein Rechenfehler, sondern ein
Bedeutungsfehler: `day` heißt im Ranking-Root etwas anderes als im Spielplan.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.models import LeagueRanking, ManagerStanding

ME = "9999999"


def _standing(manager_id: str, rank: int, season_points: int) -> ManagerStanding:
    return ManagerStanding(
        manager_id=manager_id,
        name=f"Manager {manager_id}",
        season_points=season_points,
        matchday_points=0,
        rank=rank,
        team_value=Decimal(150_000_000),
    )


def _ranking(**kwargs: object) -> LeagueRanking:
    defaults: dict[str, object] = {
        "league_id": "L1",
        "matchday": 4,
        "total_matchdays": 34,
        "managers": (
            _standing("A", 1, 4091),
            _standing("B", 2, 3497),
            _standing(ME, 3, 3311),
            _standing("C", 4, 2628),
        ),
    }
    defaults.update(kwargs)
    return LeagueRanking(**defaults)  # type: ignore[arg-type]


def test_matchdays_left_counts_from_the_last_scored_matchday() -> None:
    """`day` im Ranking ist der letzte **gewertete** Spieltag, nicht der nächste.

    Zeitgleich meldet `/competitions/1/matchdays` `day: 5` für den anstehenden.
    Gleicher Feldname, andere Bedeutung — wer sie verwechselt, rechnet um eins
    daneben, und genau diese Zahl steuert, ab wann ein Rückstand uneinholbar
    ist.
    """
    assert _ranking().matchdays_left == 30
    assert _ranking(matchday=34).matchdays_left == 0
    # Kein Überlauf, falls Kickbase während des letzten Spieltags weiterzählt.
    assert _ranking(matchday=35).matchdays_left == 0


def test_matchdays_left_is_unknown_without_the_season_length() -> None:
    """`nd` wird gelesen, nicht angenommen — eine 34 wäre in der 2. Liga falsch."""
    assert _ranking(total_matchdays=0).matchdays_left is None


def test_leader_and_neighbour_come_from_the_rank_not_the_order() -> None:
    """Die Response ist unsortiert; `min(rank)` ist der Führende, nicht `us[0]`."""
    shuffled = _ranking(
        managers=(
            _standing(ME, 3, 3311),
            _standing("C", 4, 2628),
            _standing("A", 1, 4091),
            _standing("B", 2, 3497),
        )
    )
    leader = shuffled.leader
    assert leader is not None and leader.manager_id == "A"
    ahead = shuffled.ahead_of(ME)
    assert ahead is not None and ahead.manager_id == "B"


def test_the_leader_has_nobody_ahead() -> None:
    assert _ranking().ahead_of("A") is None


def test_unknown_manager_has_no_position() -> None:
    """Fehlt die eigene Zeile in `us[]`, ist der Rang unbekannt — nicht Platz 1."""
    ranking = _ranking()
    assert ranking.standing_of("nicht-dabei") is None
    assert ranking.ahead_of("nicht-dabei") is None


def test_empty_ranking_has_no_leader() -> None:
    assert _ranking(managers=()).leader is None
