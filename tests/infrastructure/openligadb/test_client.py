"""Tests für den OpenLigaDB-Adapter mit `httpx.MockTransport` — 0 Netzwerk-I/O."""

from __future__ import annotations

import httpx
import pytest
from app.domain.gateways import ExternalDataGateway
from app.infrastructure.openligadb.client import HttpxOpenLigaDBClient
from app.infrastructure.openligadb.config import OpenLigaDBConfig
from app.infrastructure.openligadb.team_map import (
    build_team_index,
    match_opendb_team_name,
)


def _match(
    team1: str,
    team2: str,
    goals1: int,
    goals2: int,
    matchday: int,
    *,
    finished: bool = True,
) -> dict[str, object]:
    return {
        "matchID": matchday * 100 + hash(team1) % 100,
        "matchIsFinished": finished,
        "team1": {"teamId": 1, "teamName": team1, "shortName": team1[:5]},
        "team2": {"teamId": 2, "teamName": team2, "shortName": team2[:5]},
        "matchResults": [
            {"resultTypeID": 2, "pointsTeam1": goals1, "pointsTeam2": goals2},
        ],
        "group": {"groupOrderID": matchday},
    }


def _client(
    payload: list[dict[str, object]],
    *,
    config: OpenLigaDBConfig | None = None,
) -> HttpxOpenLigaDBClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/getmatchdata/bl1/")
        return httpx.Response(200, json=payload)

    return HttpxOpenLigaDBClient(config=config, transport=httpx.MockTransport(handler))


class TestTeamMap:
    def test_known_team_id_returns_aliases(self) -> None:
        aliases = build_team_index(["10"])
        assert any("bayern" in normalized for normalized in aliases)

    def test_unknown_id_produces_no_entries(self) -> None:
        assert build_team_index(["9999"]) == {}

    def test_match_opendb_direct_hit(self) -> None:
        index = build_team_index(["10"])
        assert match_opendb_team_name("FC Bayern München", index) == "10"

    def test_match_opendb_substring_fallback(self) -> None:
        # OpenLigaDB liefert manchmal längere Klarnamen — der Substring-Match
        # muss trotzdem greifen, damit der Alias „Bayern München" zieht.
        index = build_team_index(["10"])
        assert match_opendb_team_name("FC Bayern München AG", index) == "10"

    def test_match_opendb_unknown_returns_none(self) -> None:
        index = build_team_index(["10"])
        assert match_opendb_team_name("FC Sankt Nikolaus", index) is None


async def test_client_conforms_to_gateway_protocol() -> None:
    client = _client([])
    assert isinstance(client, ExternalDataGateway)
    await client.aclose()


async def test_returns_empty_when_no_team_ids_requested() -> None:
    client = _client([_match("FC Bayern München", "Borussia Dortmund", 2, 1, 1)])
    signals = await client.get_team_signals([])
    assert signals == {}
    await client.aclose()


async def test_computes_form_signal_from_recent_wins() -> None:
    payload = [
        _match("FC Bayern München", "Borussia Dortmund", 3, 0, 1),
        _match("FC Bayern München", "VfL Wolfsburg", 2, 1, 2),
        _match("FC Bayern München", "SC Freiburg", 1, 0, 3),
    ]
    client = _client(payload, config=OpenLigaDBConfig(form_window_matches=3))
    signals = await client.get_team_signals(["10"])
    # 3 Siege aus 3 Spielen → 9 Punkte / 9 max → 1.0
    assert signals["10"] == pytest.approx(1.0)
    await client.aclose()


async def test_form_signal_reflects_mixed_results() -> None:
    payload = [
        _match("FC Bayern München", "Borussia Dortmund", 3, 0, 1),  # Sieg
        _match("VfL Wolfsburg", "FC Bayern München", 2, 2, 2),  # Remis
        _match("FC Bayern München", "SC Freiburg", 0, 2, 3),  # Niederlage
    ]
    client = _client(payload, config=OpenLigaDBConfig(form_window_matches=3))
    signals = await client.get_team_signals(["10"])
    # 3 + 1 + 0 = 4 Punkte aus 3 Spielen; max 9 → 4/9
    assert signals["10"] == pytest.approx(4 / 9)
    await client.aclose()


async def test_unknown_team_absent_from_signals() -> None:
    payload = [_match("FC Bayern München", "Borussia Dortmund", 1, 0, 1)]
    client = _client(payload)
    signals = await client.get_team_signals(["10", "9999"])
    assert "10" in signals
    assert "9999" not in signals
    await client.aclose()


async def test_unfinished_matches_ignored() -> None:
    payload = [
        _match("FC Bayern München", "Borussia Dortmund", 3, 0, 1),
        _match("FC Bayern München", "VfL Wolfsburg", 5, 0, 2, finished=False),
    ]
    client = _client(payload, config=OpenLigaDBConfig(form_window_matches=3))
    signals = await client.get_team_signals(["10"])
    # Nur 1 Sieg gezählt → 3/3 = 1.0 bei effective_max = 3 (min(1, 3) * 3)
    assert signals["10"] == pytest.approx(1.0)
    await client.aclose()


async def test_upstream_error_returns_empty_signals() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = HttpxOpenLigaDBClient(transport=httpx.MockTransport(handler))
    signals = await client.get_team_signals(["10"])
    assert signals == {}
    await client.aclose()


async def test_cache_prevents_second_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[_match("FC Bayern München", "Borussia Dortmund", 1, 0, 1)])

    client = HttpxOpenLigaDBClient(transport=httpx.MockTransport(handler))
    await client.get_team_signals(["10"])
    await client.get_team_signals(["10"])
    assert calls == 1
    await client.aclose()
