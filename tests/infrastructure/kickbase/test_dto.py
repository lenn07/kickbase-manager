"""DTO-Mapping-Tests — verifizieren, dass Wire-Format → Domain funktioniert."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.models import PlayerStatus, Position
from app.infrastructure.kickbase.dto import (
    LeagueMeDTO,
    LoginResponseDTO,
    MarketResponseDTO,
    MatchdaysResponseDTO,
    PlayerDetailDTO,
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


def test_squad_v4_uses_pi_pn() -> None:
    """Spieler-IDs stehen unter `pi`/`pn`.

    Budget und Teamwert kommen **nicht** von hier: `/squad` liefert beides nicht.
    Seit P0-1 hat `Squad` die Felder gar nicht mehr — die Default-0 war D1.
    """
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


def test_market_v4_extracts_seller_id_from_user_object() -> None:
    # Kickbase liefert `u` seit einem API-Update teils als User-Objekt statt String.
    payload = {
        "it": [
            {
                "i": "4320433",
                "fn": "Leon",
                "n": "Example",
                "tid": "3",
                "pos": 2,
                "st": 0,
                "mv": "1000000",
                "prc": "1200000",
                "exs": 3600,
                "u": {"i": "999", "n": "Manager", "vft": 0, "st": 0},
            }
        ]
    }
    market = MarketResponseDTO.model_validate(payload)
    mp = market.it[0].to_market_player()
    assert mp.seller_id == "999"


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


def test_market_root_fields_become_a_snapshot() -> None:
    """Die Root-Felder der Market-Response sind Domain-Daten, kein Beiwerk.

    `tv` trägt die 33 %-Regel, `dt` den Spieltagsstart, `mvud` die zweite Uhr.
    Vor P0-1 hat das DTO nur `it` gelesen und alles andere verworfen (D1).
    """
    payload = {
        "nps": 8,
        "tv": 148767974,
        "mvud": "2026-09-23T20:00:00Z",
        "dt": "2026-10-09T18:30:00Z",
        "day": 5,
        "sn": "26/27",
        "it": [{"i": "43", "pos": 2, "st": 0, "mv": "6779912", "prc": "6779912", "exs": 16176}],
    }
    snapshot = MarketResponseDTO.model_validate(payload).to_domain()

    assert snapshot.team_value == Decimal(148767974)
    assert snapshot.mv_update_at is not None
    assert snapshot.mv_update_at.hour == 20  # 20:00 UTC = 22:00 Europe/Berlin
    assert snapshot.next_matchday_start is not None
    assert snapshot.next_matchday_start.day == 9
    assert snapshot.matchday == 5
    assert snapshot.squad_size == 8
    assert snapshot.season == "26/27"
    assert len(snapshot.players) == 1


def test_market_snapshot_survives_missing_root_fields() -> None:
    """Fehlt ein Root-Feld, wird es None/0 — aber nie geraten.

    Ein erfundener Teamwert wäre schlimmer als gar keiner: die 33 %-Regel
    rechnet dann mit einer Fantasie-Basis.
    """
    snapshot = MarketResponseDTO.model_validate({"it": []}).to_domain()
    assert snapshot.team_value == Decimal(0)
    assert snapshot.mv_update_at is None
    assert snapshot.next_matchday_start is None


def test_market_player_keeps_the_raw_expiry_seconds() -> None:
    """`exs` bleibt roh — die Uhr gehört in den Kontext, nicht in die DTO-Schicht.

    Mit `datetime.now()` im DTO war der USER-JSON nicht reproduzierbar und der
    Payload-Snapshot bei jedem Lauf rot (Plan §6/P0-0.4).
    """
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "75", "pos": 1, "mv": "500000", "prc": "500000", "exs": 3600}]}
    ).it[0]
    mp = dto.to_market_player()

    assert mp.expires_in_s == 3600
    now = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
    assert mp.expires_at(now) == datetime(2026, 9, 23, 17, 0, tzinfo=UTC)


def test_own_listing_without_exs_has_no_expiry() -> None:
    """Eigene Listings tragen kein `exs` (Plan §8/F5) — sie laufen nicht ab.

    `None` muss „unbefristet" heißen, nicht „schon abgelaufen".
    """
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "1809", "pos": 3, "mv": "8811078", "prc": "9200000"}]}
    ).it[0]
    mp = dto.to_market_player()

    assert mp.expires_in_s is None
    assert mp.expires_at(datetime.now(UTC)) is None


# -- P0-2: Gebote ---------------------------------------------------------


def test_market_item_carries_the_offer_count() -> None:
    """`ofc` ist bis zur Klärung von F1 die einzige echte Gebots-Information."""
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "1809", "pos": 3, "mv": "8811078", "prc": "9200000", "ofc": 3}]}
    ).it[0]
    mp = dto.to_market_player()

    assert mp.offer_count == 3
    assert mp.has_offers is True
    # Das Array bleibt leer — der Feldname ist ungeklärt, und ein geratenes
    # Array würde `ACCEPT_OFFER` mit erfundener ID freischalten.
    assert mp.offers == ()


def test_listing_without_offers_reports_none() -> None:
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "43", "pos": 2, "mv": "6779912", "prc": "6779912", "ofc": 0}]}
    ).it[0]
    mp = dto.to_market_player()

    assert mp.offer_count == 0
    assert mp.has_offers is False


def test_unknown_fields_surface_the_offer_array_candidate() -> None:
    """Der Feldname des Gebots-Arrays soll sich beim ersten echten Gebot zeigen.

    Ohne `extra="allow"` würde pydantic ihn still verschlucken, und F1 bliebe
    von einem manuell getimten Skriptlauf abhängig — während ein Gebot offen
    ist, und Gebote laufen ab.
    """
    dto = MarketResponseDTO.model_validate(
        {
            "it": [
                {
                    "i": "1809",
                    "pos": 3,
                    "mv": "8811078",
                    "prc": "9200000",
                    "ofc": 1,
                    "ofs": [{"i": "o1", "u": "8012345", "prc": 9300000}],
                }
            ]
        }
    ).it[0]

    unknown = dto.unknown_fields()
    assert "ofs" in unknown
    assert unknown["ofs"][0]["prc"] == 9300000
    # Bekannte Felder tauchen nicht als „unbekannt" auf.
    assert "prc" not in unknown
    assert "ofc" not in unknown


def test_known_fields_alone_leave_nothing_unknown() -> None:
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "43", "fn": "Mitchell", "n": "Weiser", "tid": "10", "pos": 2, "st": 0}]}
    ).it[0]
    assert dto.unknown_fields() == {}


# -- P0-3: Leistungsdaten, `prob` und unbekannter Status ------------------


def test_market_item_carries_performance_and_prob() -> None:
    """Bis P0-3 setzte `to_market_player()` beides hart auf 0 (Defekt D4)."""
    dto = MarketResponseDTO.model_validate(
        {
            "it": [
                {
                    "i": "43",
                    "pos": 2,
                    "st": 0,
                    "mv": "6779912",
                    "prc": "6779912",
                    "p": 141,
                    "ap": 71,
                    "prob": 1,
                    "isn": True,
                    "dt": "2026-09-23T02:01:35Z",
                }
            ]
        }
    ).it[0]
    mp = dto.to_market_player()

    assert mp.player.average_points == 71
    assert mp.player.total_points == 141
    assert mp.start_probability_raw == 1
    assert mp.is_new is True
    assert mp.listed_at is not None


def test_missing_performance_stays_none_instead_of_zero() -> None:
    """4 von 21 Marktspielern tragen weder `ap` noch `p`.

    0.0 hieße „hat gespielt und nichts gebracht" — eine andere Aussage als
    „wir wissen nichts". §9 des Plans: fehlendes Feld ⇒ None, nie Default-0.
    """
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "157", "pos": 2, "st": 0, "mv": "2821595", "prc": "2821595"}]}
    ).it[0]
    mp = dto.to_market_player()

    assert mp.player.average_points is None
    assert mp.player.total_points is None
    assert mp.start_probability_raw is None


def test_negative_season_average_survives_the_mapping() -> None:
    """Ein Minuswert ist echt (Platzverweis, Eigentor) und darf nicht wegfallen."""
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "11100", "pos": 3, "st": 0, "mv": "5813189", "p": -60, "ap": -60}]}
    ).it[0]
    assert dto.to_market_player().player.average_points == -60


def test_unknown_status_does_not_become_fit() -> None:
    """Defekt D6: `st: 128` sah als `FIT` aus und wurde aufgestellt.

    Die `st`-Liste ist nicht abschließbar (Plan §8/F3) — deshalb muss
    Unbekanntes als unbekannt durchkommen, nicht als „spielt".
    """
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "999", "pos": 3, "st": 128, "mv": "1000000"}]}
    ).it[0]
    assert dto.to_market_player().player.status is PlayerStatus.UNKNOWN


def test_known_statuses_still_map_exactly() -> None:
    for raw, expected in (
        (0, PlayerStatus.FIT),
        (2, PlayerStatus.UNKNOWN_2),
        (4, PlayerStatus.OUT_OF_SQUAD),
    ):
        dto = MarketResponseDTO.model_validate(
            {"it": [{"i": "1", "pos": 3, "st": raw, "mv": "1000000"}]}
        ).it[0]
        assert dto.to_market_player().player.status is expected


def test_status_minus_one_from_the_wire_is_not_silently_accepted() -> None:
    """`UNKNOWN` ist ein Domain-Zustand, kein Wire-Wert.

    Käme er je über die Leitung, wäre das eine API-Änderung und kein bekannter
    Status — er darf nicht als „kennen wir" durchrutschen.
    """
    dto = MarketResponseDTO.model_validate(
        {"it": [{"i": "1", "pos": 3, "st": -1, "mv": "1000000"}]}
    ).it[0]
    assert dto.to_market_player().player.status is PlayerStatus.UNKNOWN


def test_player_detail_maps_the_lineup_prediction() -> None:
    """`sl` ist die ganzjährige Quelle — `prob` fehlt ausserhalb der Spieltagswoche."""
    detail = PlayerDetailDTO.model_validate(
        {"i": "1991", "sl": True, "plpt": "Ligainsider"}
    ).to_domain("1991")

    assert detail.player_id == "1991"
    assert detail.is_predicted_starter is True
    assert detail.prediction_source == "Ligainsider"


def test_player_detail_without_prediction_stays_none() -> None:
    detail = PlayerDetailDTO.model_validate({"i": "1991"}).to_domain("1991")
    assert detail.is_predicted_starter is None
