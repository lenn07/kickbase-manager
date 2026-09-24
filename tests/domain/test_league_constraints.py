"""P1-9 — Liga-Limits lesen statt hartkodieren (Defekt D10).

Der Plan nennt als Quelle `GET /v4/leagues/{l}/settings`. Den Endpunkt gibt es
nicht; er antwortet mit HTTP 500 `NotFound`, was der Plan an anderer Stelle
(§3.4, §10) bereits festhält. Abrufbar ist nur, was `/me` liefert: `mppu` und
`tpc[]`.

Damit zerfällt das Paket in zwei Teile, und beide müssen geprüft werden:
das Kaderlimit, das jetzt echt ist — und die drei Felder, für die es **keine**
Quelle gibt und die deshalb ehrlich `null` bleiben müssen.
"""

from __future__ import annotations

from app.domain.models import LeagueConstraints
from app.infrastructure.kickbase.dto import LeagueMeDTO

from tests.infrastructure.kickbase.vcr_config import load_cassette_payload


def _league_me():  # type: ignore[no-untyped-def]
    return LeagueMeDTO.model_validate(load_cassette_payload("league_me")).to_domain("L1")


def test_squad_limit_comes_from_the_payload_not_from_a_constant() -> None:
    """`mppu` ist 16, die alte Konstante war 15.

    Eine Differenz von einem Spieler klingt klein, ist aber genau der Fall, in
    dem der Bot einen möglichen Kauf für unmöglich hält — und zwar dauerhaft,
    weil er die Grenze nie erreicht sieht.
    """
    me = _league_me()
    assert me.squad_limit == 16
    assert me.squad_limit != 15


def test_players_per_club_is_read_from_tpc() -> None:
    me = _league_me()
    assert me.players_per_club == {"2": 2, "28": 1, "29": 1, "4": 1, "7": 1, "13": 2}
    assert sum(me.players_per_club.values()) == 8


def test_squad_room_counts_open_bids() -> None:
    """Offene Gebote belegen Kaderplätze, bevor sie zuschlagen.

    Kickbase rechnet sie gegen das Limit (Plan §2.2, Regel 4) — wer sie
    ausklammert, setzt ein Gebot ab, das abgelehnt wird, und verbrennt den
    Tick.
    """
    limits = LeagueConstraints(squad_limit=16)
    assert limits.squad_room_left(squad_size=14) == 2
    assert limits.squad_room_left(squad_size=14, open_bids=2) == 0
    # Nie negativ: „schon drüber" ist derselbe Zustand wie „voll".
    assert limits.squad_room_left(squad_size=18) == 0


def test_club_limit_violation_is_detected_when_the_limit_is_known() -> None:
    """Der Test, den der Plan verlangt — für den Fall, dass ein Limit vorliegt."""
    limits = LeagueConstraints(club_limit=2, players_per_club={"2": 1, "13": 2})
    assert limits.club_room_left("2") == 1
    assert limits.club_room_left("13") == 0
    # Mit einem offenen Gebot auf denselben Verein ist auch der letzte Platz weg.
    assert limits.club_room_left("2", open_bids_for_club=1) == 0
    # Ein Verein ohne eigene Spieler hat das volle Kontingent.
    assert limits.club_room_left("99") == 2


def test_unknown_limits_say_unknown_instead_of_zero() -> None:
    """Die zweite Hälfte desselben Tests — und die wichtigere.

    Ohne bekanntes Limit darf der Code **keinen** Verstoß behaupten. Gäbe
    `club_room_left` hier 0 zurück, kaufte der Bot nie wieder einen zweiten
    Spieler desselben Vereins — eine Selbstblockade, die niemand als Fehler
    erkennen würde, weil sie wie eine Regel aussieht.
    """
    limits = LeagueConstraints(players_per_club={"2": 5})
    assert limits.club_room_left("2") is None
    assert limits.squad_room_left(squad_size=99) is None


def test_defaults_mean_nothing_known_not_nothing_allowed() -> None:
    limits = LeagueConstraints()
    assert limits.squad_limit is None
    assert limits.club_limit is None
    assert limits.underpay_blocked is None
    assert limits.scoring_mode is None
