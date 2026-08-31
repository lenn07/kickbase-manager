"""DTO-Mapping-Tests — verifizieren, dass Wire-Format → Domain funktioniert."""

from decimal import Decimal

from app.domain.models import Position
from app.infrastructure.kickbase.dto import (
    LoginResponseDTO,
    MarketResponseDTO,
    SquadResponseDTO,
)


def test_login_response_maps_to_session() -> None:
    payload = {
        "token": "abc.def.ghi",
        "tokenExp": "2026-12-31T23:59:59+00:00",
        "user": {"id": "u42", "email": "a@b.de", "name": "Lenn"},
    }
    session = LoginResponseDTO.model_validate(payload).to_session()

    assert session.token == "abc.def.ghi"
    assert session.user_id == "u42"
    assert session.email == "a@b.de"


def test_squad_maps_kickbase_short_field_names() -> None:
    payload = {
        "tv": "50000000",
        "b": "3000000",
        "it": [
            {
                "i": "p1",
                "fn": "Erling",
                "n": "Haaland",
                "tid": "t1",
                "pos": 4,
                "st": 0,
                "mv": "25000000",
                "ap": 180.5,
                "tp": 2200,
                "p": "20000000",
            }
        ],
    }
    squad = SquadResponseDTO.model_validate(payload).to_domain("L1", "M1")

    assert squad.team_value == Decimal("50000000")
    assert squad.budget == Decimal("3000000")
    assert len(squad.players) == 1
    sp = squad.players[0]
    assert sp.player.last_name == "Haaland"
    assert sp.player.position == Position.FORWARD
    assert sp.buy_price == Decimal("20000000")


def test_market_maps_offers_and_seller() -> None:
    payload = {
        "it": [
            {
                "i": "p2",
                "n": "Musiala",
                "pos": 3,
                "st": 0,
                "mv": "18000000",
                "prc": "19500000",
                "u": "seller_uid",
                "ofs": [{"i": "o1", "uid": "bidder_uid", "un": "Bidder", "p": "19000000"}],
            }
        ]
    }
    market = MarketResponseDTO.model_validate(payload)

    assert len(market.it) == 1
    mp = market.it[0].to_market_player()
    assert mp.seller_id == "seller_uid"
    assert mp.price == Decimal("19500000")
    assert len(mp.offers) == 1
    assert mp.offers[0].price == Decimal("19000000")


def test_unknown_fields_are_ignored() -> None:
    payload = {
        "token": "t",
        "tokenExp": "2026-01-01T00:00:00+00:00",
        "user": {"id": "u", "email": "e"},
        "some_new_field_from_future_api": {"nested": True},
    }
    session = LoginResponseDTO.model_validate(payload).to_session()
    assert session.token == "t"
