"""Verifiziert, dass Domain-Modelle wirklich immutable + hashable sind."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.domain.models import (
    Player,
    PlayerStatus,
    Position,
    Session,
    Squad,
    SquadPlayer,
)


def _make_player(pid: str = "p1") -> Player:
    return Player(
        id=pid,
        first_name="Erling",
        last_name="Haaland",
        team_id="t1",
        position=Position.FORWARD,
        status=PlayerStatus.FIT,
        market_value=Decimal("25000000"),
        average_points=180.5,
        total_points=2200,
    )


def test_player_is_frozen() -> None:
    player = _make_player()
    with pytest.raises(AttributeError):
        player.first_name = "Manuel"  # type: ignore[misc]


def test_player_is_hashable() -> None:
    a, b = _make_player("p1"), _make_player("p1")
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_squad_players_are_immutable_tuple() -> None:
    squad = Squad(
        league_id="L1",
        manager_id="M1",
        players=(SquadPlayer(player=_make_player(), buy_price=Decimal("20000000")),),
        team_value=Decimal("50000000"),
        budget=Decimal("5000000"),
    )
    assert isinstance(squad.players, tuple)
    with pytest.raises(TypeError):
        squad.players[0] = SquadPlayer(  # type: ignore[index]
            player=_make_player("p2"), buy_price=Decimal(0)
        )


def test_session_stores_token_and_user() -> None:
    session = Session(
        token="jwt.token.here",
        token_expires_at=datetime(2026, 12, 31, tzinfo=UTC),
        user_id="u1",
        email="user@example.com",
    )
    assert session.token == "jwt.token.here"
    assert session.email == "user@example.com"
