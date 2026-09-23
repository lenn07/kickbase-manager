"""End-to-End-Tests gegen aufgenommene Cassettes von der echten Kickbase-API.

record_mode='none' → **keine** echten Netzwerk-Requests; Tests laufen offline in CI.
Zum Neu-Aufnehmen: `python -m scripts.record_cassettes`.

Die Cassettes wurden mit fake IDs geschrieben (siehe vcr_config.py):
- User-ID:   9999999 (echt: <redacted>)
- League-ID: 1111111 (echt: <redacted>)
"""

from __future__ import annotations

import pytest
from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.kickbase.config import KickbaseClientConfig

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    make_vcr,
)


@pytest.fixture(autouse=True)
def _pretend_session_is_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cassettes tragen ein fixes Ablaufdatum vom Aufnahmezeitpunkt.

    Sobald die Wall-Clock den Cassette-Ablauf überholt, würde `_ensure_session`
    einen Relogin auslösen und nach einer nicht existierenden Login-Cassette
    suchen. Für Wire-Format-Regressionstests ist das Session-Ablaufverhalten
    irrelevant — das lebt in test_client.py mit synthetischen Sessions.
    """
    monkeypatch.setattr(
        HttpxKickbaseClient,
        "_is_session_expired",
        lambda self, session: False,
    )


def _fast_config() -> KickbaseClientConfig:
    return KickbaseClientConfig(max_requests_per_minute=10_000, jitter_min_s=0.0, jitter_max_s=0.0)


async def test_login_replays_and_returns_session() -> None:
    vcr_ = make_vcr("login")
    with vcr_.use_cassette("login.yaml"):
        async with HttpxKickbaseClient(config=_fast_config()) as client:
            session = await client.login("redacted@example.com", "REDACTED_PASSWORD")

    assert session.user_id == FAKE_USER_ID
    assert session.token == "REDACTED_JWT"
    assert session.token_expires_at.year == 2026


async def test_list_leagues_replays_users_league() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("leagues_selection").use_cassette("leagues_selection.yaml"):
            leagues = await client.list_leagues()

    assert len(leagues) == 1
    assert leagues[0].id == FAKE_LEAGUE_ID
    assert leagues[0].name
    # `b` darf negativ sein — ein Konto im Minus ist in Kickbase der Normalfall.
    # Geprüft wird nur, dass das Feld ankommt und als Decimal geparst wird.
    assert leagues[0].budget is not None


async def test_get_squad_replays_all_players() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("squad").use_cassette("squad.yaml"):
            squad = await client.get_squad(FAKE_LEAGUE_ID, FAKE_USER_ID)

    # Bewusst keine feste Kadergröße: die ändert sich mit jedem Transfer und
    # sagt nichts über das Wire-Format. Geprüft wird die Struktur.
    assert squad.players
    assert squad.manager_id == FAKE_USER_ID
    for sp in squad.players:
        assert sp.player.id != ""
        assert sp.player.last_name != ""
        assert sp.player.market_value > 0
        assert sp.player.total_points >= 0


async def test_get_market_replays_all_offers() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("market").use_cassette("market.yaml"):
            snapshot = await client.get_market(FAKE_LEAGUE_ID)

    # Die Anzahl der Angebote schwankt stündlich — geprüft wird das Wire-Format.
    assert snapshot.players
    for mp in snapshot.players:
        assert mp.price >= 0
        assert mp.player.id != ""
        assert mp.player.market_value > 0
    # Kickbase-eigene Listings haben keinen Verkäufer, eigene/fremde schon.
    assert any(mp.seller_id is None for mp in snapshot.players)


async def test_get_market_carries_the_root_fields() -> None:
    """Die Root-Felder sind der eigentliche Fund: sie tragen die 33 %-Regel.

    `tv` ist die Basis des Minus-Spielraums, `dt` der Spieltagsstart (spart den
    `list_matchdays`-Call) und `mvud` das nächste Marktwert-Update. Bis P0-1 hat
    der Client sie alle weggeworfen (Defekt D1).
    """
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("market").use_cassette("market.yaml"):
            snapshot = await client.get_market(FAKE_LEAGUE_ID)

    assert snapshot.team_value > 0
    assert snapshot.mv_update_at is not None
    assert snapshot.next_matchday_start is not None
    # `mvud` ist der *nächste* Update-Zeitpunkt (Plan §8/F4) und liegt bei
    # 20:00 UTC = 22:00 Europe/Berlin.
    assert snapshot.mv_update_at.hour == 20
    assert snapshot.matchday > 0
    assert snapshot.season


async def test_list_matchdays_replays_full_season() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("matchdays").use_cassette("matchdays.yaml"):
            matchdays = await client.list_matchdays()

    assert len(matchdays) == 34  # Bundesliga hat 34 Spieltage
    current = [m for m in matchdays if m.is_current]
    assert len(current) == 1


async def test_get_market_value_history_replays_seven_points() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("market_value").use_cassette("market_value.yaml"):
            history = await client.get_market_value_history(
                FAKE_LEAGUE_ID, player_id="1991", days=7
            )

    # Kickbase gibt am /marketvalue/{n}-Endpoint faktisch nur die 365-Tage-Serie
    # zurück; der Client schneidet auf `days=7` und liefert die letzten Punkte.
    assert len(history) == 7
    assert all(p.value > 0 for p in history)
    # Chronologisch aufsteigend (ältester zuerst)
    days = [p.day for p in history]
    assert days == sorted(days)


async def test_squad_carries_no_budget_or_team_value_fields() -> None:
    """`Squad` hat weder Budget noch Teamwert — und darf sie nie zurückbekommen.

    Beide standen dort mit Default 0, obwohl `/squad` sie nicht liefert: das war
    Defekt D1. Der Kontostand kommt aus `get_league_me()`, der Mannschaftswert
    aus dem Market-Root (`MarketSnapshot.team_value`).
    """
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("squad").use_cassette("squad.yaml"):
            squad = await client.get_squad(FAKE_LEAGUE_ID, FAKE_USER_ID)

    assert not hasattr(squad, "budget")
    assert not hasattr(squad, "team_value")


async def test_get_player_detail_replays_the_lineup_prediction() -> None:
    """`sl` ist Stufe 2 der Startelf-Kette und die einzige ganzjährige Quelle.

    `prob` liefert Kickbase nur in der Spieltagswoche (Plan §8/F2) — ohne
    diesen Endpunkt bliebe D5 an 11 von 14 Tagen unbehoben.
    """
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("player_detail").use_cassette("player_detail.yaml"):
            detail = await client.get_player_detail(FAKE_LEAGUE_ID, "1991")

    assert detail.player_id == "1991"
    assert detail.is_predicted_starter is not None
    # Die Herkunft wandert mit ins USER-JSON: eine Prognose ohne Quelle kann
    # das Modell nicht gewichten.
    assert detail.prediction_source
