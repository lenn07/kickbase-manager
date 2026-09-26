"""Die Saison-Uhr des Prompts (P2-14).

`trading.season_phase` kippt die Zielhierarchie: acht Restspieltage vor
Schluss fällt das Marktwert-Trading aus Ziel 3 heraus, weil die Kette aus
Kaufen, Halten, Verkaufen und Nachkaufen nicht mehr durchreicht. Ein Konto
voller Geld ist am letzten Spieltag exakt null Punkte wert.

Die Schwelle ist eine **Design-Entscheidung**, keine API-Semantik — deshalb
steht sie als benannte Konstante im Code und wird hier an ihren Rändern
festgehalten. Wer sie verschiebt, verschiebt sie sichtbar.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.application.ai_decision_engine import _SEASON_ENDGAME_MATCHDAYS_LEFT, _season_phase
from app.application.decision_engine import DecisionContext
from app.domain.models import LeagueMe, LeagueRanking, ManagerStanding, Squad

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
LEAGUE_ID = "L1"
MANAGER_ID = "m1"


def _context(*, matchday: int | None, total_matchdays: int = 34) -> DecisionContext:
    ranking = (
        None
        if matchday is None
        else LeagueRanking(
            league_id=LEAGUE_ID,
            matchday=matchday,
            total_matchdays=total_matchdays,
            managers=(
                ManagerStanding(
                    manager_id=MANAGER_ID,
                    name="Ich",
                    season_points=1000,
                    matchday_points=0,
                    rank=1,
                    team_value=Decimal(100),
                ),
            ),
        )
    )
    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal(0)),
        squad=Squad(league_id=LEAGUE_ID, manager_id=MANAGER_ID, players=()),
        market=(),
        budget=Decimal(0),
        min_action_score=0.6,
        max_trade_pct=0.25,
        min_cash_reserve=0,
        now=NOW,
        league_ranking=ranking,
    )


def test_mid_season_is_regular() -> None:
    assert _season_phase(_context(matchday=4)) == "regular"


def test_the_endgame_starts_at_the_documented_threshold() -> None:
    """Acht Restspieltage, also ab Spieltag 26 von 34 — der Rand beidseitig."""
    first_endgame = 34 - _SEASON_ENDGAME_MATCHDAYS_LEFT  # 26
    assert _season_phase(_context(matchday=first_endgame - 1)) == "regular"
    assert _season_phase(_context(matchday=first_endgame)) == "endgame"


def test_the_last_matchday_is_still_endgame_not_over() -> None:
    """Solange ein Spieltag aussteht, zählen Punkte — Trading aber nicht mehr."""
    assert _season_phase(_context(matchday=33)) == "endgame"


def test_season_over_when_nothing_is_left() -> None:
    assert _season_phase(_context(matchday=34)) == "over"


def test_unknown_without_a_ranking() -> None:
    """Ohne Ligatabelle keine Saison-Uhr — und kein geratener Default.

    `regular` als Fallback wäre bequem und falsch: der Bot würde im Mai
    weitertraden, nur weil ein HTTP-Call ausgefallen ist. Das Label sagt
    stattdessen, dass es unbekannt ist; der Prompt entscheidet daraufhin
    konservativ und vermerkt das Flag.
    """
    assert _season_phase(_context(matchday=None)) == "unknown"


def test_unknown_when_the_season_length_is_missing() -> None:
    """`nd == 0` heisst „Saisonlaenge unbekannt", nicht „Saison vorbei"."""
    assert _season_phase(_context(matchday=30, total_matchdays=0)) == "unknown"
