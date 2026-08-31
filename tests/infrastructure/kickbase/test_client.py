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


async def test_sell_player_posts_listing_and_returns_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/user/login":
            return httpx.Response(200, json=_LOGIN_OK)
        assert request.method == "POST"
        assert request.url.path == "/v4/leagues/L1/market"
        body = request.content
        assert b'"playerId":"P7"' in body
        assert b'"price":900000' in body
        return httpx.Response(200, json={"i": "listing-77"})

    async with _client(httpx.MockTransport(handler)) as client:
        await client.login("a@b.de", "pw")
        listing_id = await client.sell_player("L1", "P7", Decimal("900000"))

    assert listing_id == "listing-77"


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
