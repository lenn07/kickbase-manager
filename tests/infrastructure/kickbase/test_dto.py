"""DTO-Mapping-Tests — verifizieren, dass Wire-Format → Domain funktioniert."""

from decimal import Decimal

from app.domain.models import Position
from app.infrastructure.kickbase.dto import (
    LeagueMeDTO,
    LoginResponseDTO,
    MarketResponseDTO,
    MatchdaysResponseDTO,
    SquadResponseDTO,
)


def test_login_response_v4_uses_tknex_as_expiry() -> None:
    payload = {
        "tkn": "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjEwMDB9.sig",
        "tknex": "2026-09-07T10:51:50Z",
        "u": {"id": "4320433", "email": "a@b.de", "name": "Lenn"},
    }
    session = LoginResponseDTO.model_validate(payload).to_session()

    assert session.token.startswith("eyJ")
    assert session.user_id == "4320433"
    assert session.email == "a@b.de"
    assert session.token_expires_at.year == 2026
    assert session.token_expires_at.month == 9


def test_login_response_falls_back_to_jwt_exp_when_tknex_missing() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjIwMDAwMDAwMDB9.sig"  # exp = 2033
    payload = {"tkn": jwt, "u": {"id": "u42", "email": "a@b.de"}}
    session = LoginResponseDTO.model_validate(payload).to_session()
    assert session.token_expires_at.year == 2033


def test_login_response_legacy_format_still_parses() -> None:
    payload = {
        "token": "abc.def.ghi",
        "tokenExp": "2026-12-31T23:59:59+00:00",
        "user": {"id": "u42", "email": "a@b.de", "name": "Lenn"},
    }
    session = LoginResponseDTO.model_validate(payload).to_session()
    assert session.token == "abc.def.ghi"
    assert session.user_id == "u42"


def test_league_me_maps_budget_and_flags() -> None:
    payload = {"b": "9373464.0", "un": "24", "adm": False, "lnm": "Noob_League"}
    me = LeagueMeDTO.model_validate(payload).to_domain("L1")
    assert me.league_id == "L1"
    assert me.budget == Decimal("9373464.0")
    assert me.unread_notifications == 24
    assert me.is_admin is False


def test_squad_v4_uses_pi_pn_and_no_budget() -> None:
    """Squad-Response hat KEIN Budget-Feld — bleibt 0. Spieler-IDs sind unter `pi`/`pn`."""
    payload = {
        "u": "4320433",
        "nps": "9",
        "st": "3",
        "it": [
            {
                "pi": "1991",
                "pn": "Upamecano",
                "tid": "2",
                "pos": 2,
                "st": 0,
                "mv": "33253201",
                "ap": 275.0,
                "p": 275,
            }
        ],
    }
    squad = SquadResponseDTO.model_validate(payload).to_domain("L1", "4320433")

    assert squad.league_id == "L1"
    assert squad.manager_id == "4320433"
    assert squad.budget == Decimal(0)
    assert len(squad.players) == 1
    sp = squad.players[0]
    assert sp.player.id == "1991"
    assert sp.player.last_name == "Upamecano"
    assert sp.player.position == Position.DEFENDER
    assert sp.player.market_value == Decimal("33253201")
    assert sp.buy_price == Decimal(0)


def test_market_v4_maps_prc_exs_and_no_offers() -> None:
    payload = {
        "it": [
            {
                "i": "75",
                "fn": "Patrick",
                "n": "Drewes",
                "tid": "3",
                "pos": 1,
                "st": 0,
                "mv": "500000",
                "prc": "500000",
                "exs": 28095,
            }
        ]
    }
    market = MarketResponseDTO.model_validate(payload)

    assert len(market.it) == 1
    mp = market.it[0].to_market_player()
    assert mp.player.id == "75"
    assert mp.player.first_name == "Patrick"
    assert mp.player.last_name == "Drewes"
    assert mp.price == Decimal("500000")
    assert mp.seller_id is None
    assert mp.expires_at is not None
    assert mp.offers == ()


def test_matchdays_flattens_groups_and_marks_current() -> None:
    payload = {
        "day": 2,
        "it": [
            {
                "day": 1,
                "it": [
                    {"mi": "m1", "day": 1, "dt": "2026-08-28T18:30:00Z", "st": 2},
                    {"mi": "m2", "day": 1, "dt": "2026-08-30T15:30:00Z", "st": 2},
                ],
            },
            {
                "day": 2,
                "it": [{"mi": "m3", "day": 2, "dt": "2026-09-04T18:30:00Z", "st": 0}],
            },
        ],
    }
    matchdays = MatchdaysResponseDTO.model_validate(payload).to_domain()

    assert len(matchdays) == 2
    assert matchdays[0].number == 1
    assert matchdays[0].is_current is False
    assert matchdays[0].starts_at.day == 28
    assert matchdays[1].number == 2
    assert matchdays[1].is_current is True


def test_unknown_fields_are_ignored() -> None:
    payload = {"tkn": "t", "u": {"id": "u"}, "future_field": {"nested": True}}
    session = LoginResponseDTO.model_validate(payload).to_session()
    assert session.token == "t"
