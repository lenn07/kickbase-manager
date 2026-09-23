"""Der deterministische Startelf-Guard (P0-4).

Zwei Eigenschaften entscheiden, ob der Guard nützt oder schadet:

1. Er muss greifen, wenn Slots leer sind oder ein Aufgestellter nicht spielen
   kann — das sind die Fälle, die 100 Punkte pro Slot kosten.
2. Er muss **schweigen**, wenn alles in Ordnung ist. Ein Guard, der jeden Tick
   schreibt, erzeugt Rauschen im Log und Last gegen das Rate-Limit, ohne einen
   einzigen Punkt zu bringen.
"""

from __future__ import annotations

from decimal import Decimal

from app.application.lineup_guard import propose_lineup_fix, to_decision
from app.application.player_enrichment import PlayerEnrichment
from app.domain.lineup import Lineup
from app.domain.models import Player, PlayerStatus, Position, SquadPlayer
from app.domain.trade import TradeAction, TradeIntent


def _sp(
    pid: str,
    position: Position,
    *,
    average_points: float | None = 100.0,
    status: PlayerStatus = PlayerStatus.FIT,
    lineup_order: int | None = None,
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
        ),
        lineup_order=lineup_order,
    )


def _squad_for_352(**overrides: SquadPlayer) -> list[SquadPlayer]:
    base = {
        "gk1": _sp("gk1", Position.GOALKEEPER),
        **{f"def{i}": _sp(f"def{i}", Position.DEFENDER) for i in range(1, 4)},
        **{f"mid{i}": _sp(f"mid{i}", Position.MIDFIELDER) for i in range(1, 6)},
        **{f"fwd{i}": _sp(f"fwd{i}", Position.FORWARD) for i in range(1, 3)},
    }
    base.update(overrides)
    return list(base.values())


def _full_lineup(squad: list[SquadPlayer]) -> Lineup:
    return Lineup(formation="3-5-2", player_ids=tuple(sp.player.id for sp in squad))


def _enrichment(pid: str, probability: float | None) -> PlayerEnrichment:
    return PlayerEnrichment(
        player_id=pid,
        market_trend_1d_pct=None,
        market_trend_3d_pct=None,
        market_trend_7d_pct=None,
        market_trend_30d_pct=None,
        mv_max_30d=None,
        avg_points_last5=100.0,
        start_probability_next=probability,
        start_probability_source="kickbase_prob",
        injury_status="fit",
    )


# -- Der Guard schweigt, wenn alles passt ---------------------------------


def test_guard_stays_silent_on_a_complete_healthy_lineup() -> None:
    squad = _squad_for_352()
    assert propose_lineup_fix(squad=squad, current=_full_lineup(squad)) is None


def test_guard_stays_silent_on_an_empty_squad() -> None:
    """Ohne Spieler gibt es nichts aufzustellen — und nichts zu schreiben."""
    assert propose_lineup_fix(squad=[], current=Lineup("3-5-2", ())) is None


def test_guard_stays_silent_when_the_squad_is_simply_too_small() -> None:
    """Acht Spieler, acht besetzte Slots: mehr geht nicht, also nichts tun.

    Der reale Zustand vom 2026-09-23. Die drei fehlenden Slots löst nur ein
    Kauf — und der ist eine Entscheidung des Modells, nicht des Guards.
    """
    squad = [
        _sp("gk1", Position.GOALKEEPER),
        *[_sp(f"def{i}", Position.DEFENDER) for i in range(1, 3)],
        *[_sp(f"mid{i}", Position.MIDFIELDER) for i in range(1, 4)],
        *[_sp(f"fwd{i}", Position.FORWARD) for i in range(1, 3)],
    ]
    assert propose_lineup_fix(squad=squad, current=_full_lineup(squad)) is None


# -- Der Guard greift, wo Punkte auf dem Spiel stehen ---------------------


def test_guard_fills_empty_slots() -> None:
    squad = _squad_for_352()
    current = Lineup(formation="3-5-2", player_ids=("gk1", "def1", "def2"))

    proposal = propose_lineup_fix(squad=squad, current=current)

    assert proposal is not None
    assert len(proposal.lineup.player_ids) == 11
    assert proposal.current_count == 3
    assert "8 weitere besetzbar" in proposal.reason
    assert "100 Punkte" in proposal.reason


def test_guard_replaces_an_injured_starter_with_a_fit_substitute() -> None:
    """Verkaufsgrund ist das nicht — aber aufstellen sollte man ihn auch nicht."""
    squad = _squad_for_352(
        mid5=_sp("mid5", Position.MIDFIELDER, status=PlayerStatus.INJURED),
    )
    squad.append(_sp("bank_mid", Position.MIDFIELDER, average_points=60.0))
    current = _full_lineup([sp for sp in squad if sp.player.id != "bank_mid"])

    proposal = propose_lineup_fix(squad=squad, current=current)

    assert proposal is not None
    assert "mid5" not in proposal.lineup.player_ids
    assert "bank_mid" in proposal.lineup.player_ids
    assert "können nicht spielen" in proposal.reason


def test_guard_replaces_a_suspended_starter() -> None:
    squad = _squad_for_352(
        fwd1=_sp("fwd1", Position.FORWARD, status=PlayerStatus.RED_CARD),
    )
    squad.append(_sp("bank_fwd", Position.FORWARD, average_points=70.0))
    current = _full_lineup([sp for sp in squad if sp.player.id != "bank_fwd"])

    proposal = propose_lineup_fix(squad=squad, current=current)

    assert proposal is not None
    assert "fwd1" not in proposal.lineup.player_ids


def test_guard_keeps_an_injured_player_when_there_is_no_replacement() -> None:
    """Ein Zweifelsfall ist besser als eine Lücke: -100 sind sicher, er nicht."""
    squad = _squad_for_352(
        mid5=_sp("mid5", Position.MIDFIELDER, status=PlayerStatus.INJURED),
    )
    current = _full_lineup(squad)

    proposal = propose_lineup_fix(squad=squad, current=current)

    assert proposal is None


def test_guard_uses_start_probabilities_when_choosing() -> None:
    squad = _squad_for_352()
    squad.append(_sp("bank_fwd", Position.FORWARD, average_points=100.0))
    current = Lineup(formation="3-5-2", player_ids=("gk1",))
    enrichment = {
        "fwd1": _enrichment("fwd1", 0.05),
        "fwd2": _enrichment("fwd2", 0.95),
        "bank_fwd": _enrichment("bank_fwd", 0.95),
    }

    proposal = propose_lineup_fix(squad=squad, current=current, enrichment=enrichment)

    assert proposal is not None
    assert "fwd1" not in proposal.lineup.player_ids
    assert {"fwd2", "bank_fwd"} <= set(proposal.lineup.player_ids)


def test_guard_keeps_the_reported_formation_when_it_suffices() -> None:
    """Die gemeldete Formation ist die einzige nachweislich gültige."""
    squad = _squad_for_352()
    current = Lineup(formation="3-5-2", player_ids=("gk1",))

    proposal = propose_lineup_fix(squad=squad, current=current)

    assert proposal is not None
    assert proposal.lineup.formation == "3-5-2"


# -- Übersetzung in eine Entscheidung -------------------------------------


def test_proposal_becomes_a_set_lineup_decision() -> None:
    squad = _squad_for_352()
    proposal = propose_lineup_fix(
        squad=squad, current=Lineup(formation="3-5-2", player_ids=("gk1",))
    )
    assert proposal is not None

    decision = to_decision(proposal)

    assert decision.action is TradeAction.SET_LINEUP
    assert decision.intent is TradeIntent.POINTS
    assert decision.lineup == proposal.lineup
    assert decision.reason
