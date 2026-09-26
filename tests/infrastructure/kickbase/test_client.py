"""Client-Tests mit `httpx.MockTransport` — 0 echte Netzwerk-Aufrufe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.domain.exceptions import (
    AuthError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    TransportError,
)
from app.domain.gateways import KickbaseGateway, SessionStore
from app.domain.lineup import FORMATIONS, Lineup
from app.domain.models import Session as KbSession
from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.kickbase.config import KickbaseClientConfig

_LOGIN_OK = {"tkn": "tkn-1", "u": {"i": "u1", "em": "a@b.de", "n": "L"}}
_LOGIN_OK_2 = {"tkn": "tkn-2", "u": {"i": "u1", "em": "a@b.de", "n": "L"}}


class FakeSessionStore:
    """In-Memory-Fake — imitiert DbSessionStore ohne DB oder Vault."""

    def __init__(
        self,
        *,
        session: KbSession | None = None,
        credentials: tuple[str, str] | None = None,
    ) -> None:
        self.session = session
        self.credentials = credentials
        self.saves: list[KbSession] = []

    async def load_session(self) -> KbSession | None:
        return self.session

    async def save_session(self, session: KbSession) -> None:
        self.session = session
        self.saves.append(session)

    async def load_credentials(self) -> tuple[str, str] | None:
        return self.credentials


def _fast_config() -> KickbaseClientConfig:
    return KickbaseClientConfig(
        max_requests_per_minute=10_000,
        jitter_min_s=0.0,
        jitter_max_s=0.0,
        backoff_base_s=0.0,
    )


def _client(
    handler: httpx.MockTransport,
    *,
    session_store: SessionStore | None = None,
) -> HttpxKickbaseClient:
    return HttpxKickbaseClient(
        config=_fast_config(), transport=handler, session_store=session_store
    )


async def test_client_conforms_to_gateway_protocol() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(204)))
    assert isinstance(client, KickbaseGateway)
    await client.aclose()


async def test_login_returns_session_and_stores_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/user/login"
        assert "Authorization" not in request.headers
        # v4 erwartet em/pass, nicht email/password
        assert b'"em":"a@b.de"' in request.content
        assert b'"pass":"pw"' in request.content
        return httpx.Response(200, json=_LOGIN_OK)

    async with _client(httpx.MockTransport(handler)) as client:
        session = await client.login("a@b.de", "pw")
        assert session.token == "tkn-1"
        assert session.user_id == "u1"


async def test_authenticated_request_sends_bearer_token() -> None:
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        calls.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"it": []})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        await client.list_leagues()

    assert calls == ["Bearer tkn-1"]


async def test_401_triggers_relogin_and_retries() -> None:
    state = {"login_count": 0, "list_count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            state["login_count"] += 1
            token = _LOGIN_OK if state["login_count"] == 1 else _LOGIN_OK_2
            return httpx.Response(200, json=token)
        state["list_count"] += 1
        if state["list_count"] == 1:
            return httpx.Response(401, json={"message": "token expired"})
        assert request.headers["Authorization"] == "Bearer tkn-2"
        return httpx.Response(200, json={"it": []})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        result = await client.list_leagues()

    assert result == []
    assert state["login_count"] == 2
    assert state["list_count"] == 2


async def test_persistent_401_raises_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        return httpx.Response(401, json={"message": "nope"})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        with pytest.raises(AuthError):
            await client.list_leagues()


@pytest.mark.parametrize(
    ("code", "exc"),
    [
        (403, AuthError),
        (404, NotFoundError),
        (409, ConflictError),
        (429, RateLimitError),
    ],
)
async def test_http_errors_map_to_domain_exceptions(code: int, exc: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        return httpx.Response(code, json={"message": "boom"})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        with pytest.raises(exc):
            await client.list_leagues()


async def test_place_bid_sends_price_and_returns_offer_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        assert request.url.path == "/v4/leagues/L1/market/P1/offers"
        body = httpx.Request(request.method, request.url, content=request.content).content
        assert b'"price":1500000' in body
        return httpx.Response(200, json={"i": "offer-42"})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        offer_id = await client.place_bid("L1", "P1", Decimal("1500000"))

    assert offer_id == "offer-42"


async def test_list_on_market_posts_listing_and_returns_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        assert request.method == "POST"
        # Trailing Slash ist Pflicht — ohne ihn antwortet Kickbase mit HTTP 500.
        assert request.url.path == "/v4/leagues/L1/market/"
        body = request.content
        # Kurzformen `pi` und `prc` — lange Namen liefern Fehlercode 2 (500).
        assert b'"pi":"P7"' in body
        assert b'"prc":900000' in body
        return httpx.Response(200, json={})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        listing_ref = await client.list_on_market("L1", "P7", Decimal("900000"))

    assert listing_ref == "P7"


async def test_sell_to_kickbase_posts_to_sell_endpoint() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        await client.sell_to_kickbase("L1", "P7")

    assert seen == {"method": "POST", "path": "/v4/leagues/L1/market/P7/sell"}


async def test_remove_from_market_deletes_listing() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        await client.remove_from_market("L1", "P7")

    assert seen == {"method": "DELETE", "path": "/v4/leagues/L1/market/P7"}


async def test_authenticated_call_without_login_raises() -> None:
    async with _client(httpx.MockTransport(lambda _r: httpx.Response(200, json={}))) as client:
        with pytest.raises(AuthError):
            await client.list_leagues()


# -- Session-Reuse via SessionStore ------------------------------------


def _valid_session(token: str = "cached-token") -> KbSession:
    return KbSession(
        token=token,
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        user_id="u1",
        email="a@b.de",
    )


async def test_cached_session_is_reused_without_login() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        assert request.headers.get("Authorization") == "Bearer cached-token"
        return httpx.Response(200, json={"it": []})

    store = FakeSessionStore(session=_valid_session(), credentials=("a@b.de", "pw"))

    async with _client(httpx.MockTransport(handler), session_store=store) as client:
        result = await client.list_leagues()

    assert result == []
    assert "/v4/user/login" not in calls
    assert calls == ["/v4/leagues/selection"]


async def test_expired_cached_session_triggers_relogin() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        assert request.headers.get("Authorization") == "Bearer tkn-1"
        return httpx.Response(200, json={"it": []})

    expired = KbSession(
        token="stale",
        token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        user_id="u1",
        email="a@b.de",
    )
    store = FakeSessionStore(session=expired, credentials=("a@b.de", "pw"))

    async with _client(httpx.MockTransport(handler), session_store=store) as client:
        await client.list_leagues()

    assert calls == ["/v4/user/login", "/v4/leagues/selection"]
    # Nach Relogin muss die neue Session im Store persistiert sein.
    assert store.session is not None
    assert store.session.token == "tkn-1"
    assert store.saves and store.saves[-1].token == "tkn-1"


async def test_401_relogin_uses_credentials_from_store() -> None:
    """Nach einem 401 muss der Client mit Store-Credentials neu einloggen — auch ohne
    vorherigen expliziten `login()`-Aufruf."""
    state = {"login": 0, "list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            state["login"] += 1
            return httpx.Response(200, json=_LOGIN_OK_2)
        state["list"] += 1
        if state["list"] == 1:
            return httpx.Response(401, json={"message": "token expired"})
        assert request.headers["Authorization"] == "Bearer tkn-2"
        return httpx.Response(200, json={"it": []})

    store = FakeSessionStore(session=_valid_session(), credentials=("a@b.de", "pw"))

    async with _client(httpx.MockTransport(handler), session_store=store) as client:
        await client.list_leagues()

    assert state["login"] == 1
    assert state["list"] == 2
    assert store.session is not None
    assert store.session.token == "tkn-2"


async def test_no_cached_session_and_no_credentials_raises_auth_error() -> None:
    async with _client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
        session_store=FakeSessionStore(),
    ) as client:
        with pytest.raises(AuthError):
            await client.list_leagues()


async def test_5xx_is_retried_with_backoff_and_finally_succeeds() -> None:
    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        state["count"] += 1
        if state["count"] < 3:
            return httpx.Response(503, json={"message": "upstream down"})
        return httpx.Response(200, json={"it": []})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        result = await client.list_leagues()

    assert result == []
    assert state["count"] == 3  # 2 Retries + 1 finaler Erfolg


async def test_persistent_5xx_raises_transport_error_after_retries() -> None:
    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        state["count"] += 1
        return httpx.Response(502, json={"message": "bad gateway"})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        with pytest.raises(TransportError):
            await client.list_leagues()

    # max_retries_5xx=2 → initial + 2 retries = 3 Aufrufe.
    assert state["count"] == 3


async def test_network_error_is_retried_before_giving_up() -> None:
    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        state["count"] += 1
        if state["count"] < 2:
            raise httpx.ConnectError("temporär weg")
        return httpx.Response(200, json={"it": []})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        result = await client.list_leagues()

    assert result == []
    assert state["count"] == 2


async def test_login_saves_session_to_store() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_LOGIN_OK)

    store = FakeSessionStore()
    async with _client(httpx.MockTransport(handler), session_store=store) as client:
        await client.login("a@b.de", "pw")

    assert store.saves
    assert store.saves[-1].token == "tkn-1"


# -- P0-2: Diagnose für das ungeklärte Gebots-Array -----------------------


def _market_response(item: dict[str, object]) -> dict[str, object]:
    return {"tv": 148767974, "mvud": "2026-09-23T20:00:00Z", "day": 5, "it": [item]}


async def test_get_market_warns_about_unknown_fields_on_a_listing_with_offers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Beim ersten echten Gebot muss der Feldname des Arrays sichtbar werden.

    Sonst hängt F1 an einem manuell getimten Skriptlauf — der müsste zufällig
    laufen, *während* ein Gebot offen ist. Der Tick sieht den Payload ohnehin;
    er soll den Fund festhalten, ohne deshalb anders zu entscheiden.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        return httpx.Response(
            200,
            json=_market_response(
                {
                    "i": "1809",
                    "pos": 3,
                    "mv": 8811078,
                    "prc": 9200000,
                    "ofc": 2,
                    "ofs": [{"i": "o1", "prc": 9300000}],
                }
            ),
        )

    with caplog.at_level("INFO"):
        async with _client(httpx.MockTransport(handler)) as client:
            await client.login("a@b.de", "pw")
            snapshot = await client.get_market("L1")

    assert snapshot.players[0].offer_count == 2
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "Ein unbekanntes Feld auf einem Listing mit Geboten muss auffallen"
    assert "ofs" in warnings[0].getMessage()
    assert "1809" in warnings[0].getMessage()


async def test_get_market_stays_quiet_when_nobody_has_bid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ohne Gebot sagt ein unbekanntes Feld nichts über das Gebots-Array.

    Eine Warnung pro Tick, die nie etwas bedeutet, wird nach zwei Tagen
    ignoriert — dann geht der echte Fund darin unter.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        return httpx.Response(
            200,
            json=_market_response(
                {"i": "43", "pos": 2, "mv": 6779912, "prc": 6779912, "ofc": 0, "irgendwas": 1}
            ),
        )

    with caplog.at_level("INFO"):
        async with _client(httpx.MockTransport(handler)) as client:
            await client.login("a@b.de", "pw")
            await client.get_market("L1")

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


async def test_get_market_notes_when_offers_are_not_in_the_payload_at_all(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Auch das ist ein Befund: `ofc > 0`, aber kein einziges unbekanntes Feld.

    Dann steht das Gebots-Array nicht im `/market`-Payload, und F1 braucht eine
    andere Quelle — das muss man erfahren, nicht raten.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        return httpx.Response(
            200, json=_market_response({"i": "1809", "pos": 3, "mv": 8811078, "ofc": 1})
        )

    with caplog.at_level("INFO"):
        async with _client(httpx.MockTransport(handler)) as client:
            await client.login("a@b.de", "pw")
            await client.get_market("L1")

    messages = [r.getMessage() for r in caplog.records]
    assert any("nicht im /market-Payload" in m for m in messages)


# -- P0-4: Aufstellung ----------------------------------------------------


async def test_get_lineup_reads_formation_and_slots() -> None:
    """Ohne die Formation lässt sich keine gültige Aufstellung schreiben.

    Deshalb `/lineup/overview` statt `/lineup`: nur dort stehen `t` (System)
    und `lis` (Deadline).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        assert request.url.path == "/v4/leagues/L1/lineup/overview"
        return httpx.Response(
            200,
            json={
                "t": "3-5-2",
                "lis": "2026-10-09T18:30:00Z",
                "lpc": 3,
                "lp": [
                    {"pi": "mid1", "pos": 3, "lo": 4},
                    {"pi": "gk1", "pos": 1, "lo": 0},
                    {"pi": "def1", "pos": 2, "lo": 1},
                ],
            },
        )

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        lineup = await client.get_lineup("L1")

    assert lineup.formation == "3-5-2"
    # Slot-Reihenfolge, nicht Payload-Reihenfolge: Kickbase erwartet die IDs
    # in der Reihenfolge der Slots.
    assert lineup.player_ids == ("gk1", "def1", "mid1")


async def test_set_lineup_posts_type_and_players() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        await client.set_lineup("L1", Lineup(formation="3-5-2", player_ids=("gk1", "def1")))

    assert seen["method"] == "POST"
    assert seen["path"] == "/v4/leagues/L1/lineup"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'"type":"3-5-2"' in body
    assert b'"players":["gk1","def1"]' in body


async def test_get_lineup_without_a_formation_falls_back_to_the_default() -> None:
    """Ein leeres `t` darf nicht zu einer leeren Formation führen.

    `validate_lineup` würde die sonst als unbekannt ablehnen, und der Guard
    käme nie zum Schreiben — ausgerechnet dann, wenn noch nichts aufgestellt
    ist.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        return httpx.Response(200, json={"lp": []})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        lineup = await client.get_lineup("L1")

    assert lineup.formation in FORMATIONS
    assert lineup.player_ids == ()


async def test_competition_endpoints_hit_the_right_paths() -> None:
    """Tabelle und Spielplan (P2-11) — zwei Calls für alle Spieler.

    Beide Pfade sind ligaunabhängig (`/competitions/{id}/…`, nicht
    `/leagues/{id}/…`). Ein Tippfehler darin fällt sonst erst im Betrieb auf,
    und zwar als stilles `missing_data:fixtures` in jedem Payload.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        seen.append(request.url.path)
        if request.url.path.endswith("/table"):
            return httpx.Response(
                200,
                json={"it": [{"tid": "3", "tn": "Dortmund", "cpl": 1, "cp": 12, "mc": 4, "gd": 7}]},
            )
        return httpx.Response(
            200,
            json={
                "day": 5,
                "it": [
                    {
                        "day": 5,
                        "it": [
                            {"mi": "2", "dt": "2026-10-09T18:30:00Z", "t1": "13", "t2": "2"},
                        ],
                    }
                ],
            },
        )

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        standings = await client.get_competition_table()
        fixtures = await client.list_fixtures()

    assert seen == ["/v4/competitions/1/table", "/v4/competitions/1/matchdays"]
    assert standings[0].team_name == "Dortmund"
    assert fixtures[0].away_team_id == "2"
