"""End-to-End-Tests gegen aufgenommene Cassettes von der echten Kickbase-API.

record_mode='none' → **keine** echten Netzwerk-Requests; Tests laufen offline in CI.
Zum Neu-Aufnehmen: `python -m scripts.record_cassettes`.

Die Cassettes wurden mit fake IDs geschrieben (siehe vcr_config.py):
- User-ID:   9999999 (echt: <redacted>)
- League-ID: 1111111 (echt: <redacted>)
"""

from __future__ import annotations

from decimal import Decimal

from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.kickbase.config import KickbaseClientConfig

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    make_vcr,
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
    assert leagues[0].budget is not None and leagues[0].budget > 0


async def test_get_squad_replays_nine_players() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("squad").use_cassette("squad.yaml"):
            squad = await client.get_squad(FAKE_LEAGUE_ID, FAKE_USER_ID)

    assert len(squad.players) == 9
    # Alle Spieler haben eine sinnvolle ID + market_value > 0
    for sp in squad.players:
        assert sp.player.id != ""
        assert sp.player.market_value > 0


async def test_get_market_replays_all_offers() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("market").use_cassette("market.yaml"):
            market = await client.get_market(FAKE_LEAGUE_ID)

    assert len(market) == 22
    for mp in market:
        assert mp.price >= 0
        assert mp.player.id != ""


async def test_list_matchdays_replays_full_season() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("matchdays").use_cassette("matchdays.yaml"):
            matchdays = await client.list_matchdays()

    assert len(matchdays) == 34  # Bundesliga hat 34 Spieltage
    current = [m for m in matchdays if m.is_current]
    assert len(current) == 1


async def test_get_market_value_history_replays_empty_series() -> None:
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("market_value").use_cassette("market_value.yaml"):
            history = await client.get_market_value_history(
                FAKE_LEAGUE_ID, player_id="1991", days=7
            )

    # Bei diesem Spieler war die Response leer — bestätigt korrektes Parsing
    # eines leeren `it`-Arrays ohne Crash.
    assert history == []
    assert isinstance(history, list)


async def test_get_squad_returns_zero_budget_since_not_in_response() -> None:
    """Squad-Response liefert kein Budget → Domain-Default 0. Budget kommt
    aus separater get_league_me()-Abfrage."""
    async with HttpxKickbaseClient(config=_fast_config()) as client:
        with make_vcr("login").use_cassette("login.yaml"):
            await client.login("redacted@example.com", "REDACTED_PASSWORD")
        with make_vcr("squad").use_cassette("squad.yaml"):
            squad = await client.get_squad(FAKE_LEAGUE_ID, FAKE_USER_ID)

    assert squad.budget == Decimal(0)
