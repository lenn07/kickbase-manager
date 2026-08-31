"""Async-HTTP-Adapter für Kickbase v4 — implementiert `KickbaseGateway`."""

from __future__ import annotations

import logging
from decimal import Decimal
from http import HTTPStatus
from typing import Any

import httpx

from app.domain.exceptions import (
    AuthError,
    ConflictError,
    KickbaseError,
    NotFoundError,
    RateLimitError,
    TransportError,
)
from app.domain.models import (
    League,
    MarketPlayer,
    MarketValuePoint,
    Matchday,
    Session,
    Squad,
)
from app.infrastructure.kickbase.config import KickbaseClientConfig
from app.infrastructure.kickbase.dto import (
    BidResponseDTO,
    LeagueSelectionDTO,
    LoginResponseDTO,
    MarketResponseDTO,
    MarketValueResponseDTO,
    MatchdaysResponseDTO,
    SquadResponseDTO,
)
from app.infrastructure.kickbase.rate_limit import AsyncRateLimiter

_log = logging.getLogger(__name__)


class HttpxKickbaseClient:
    """Konkrete `KickbaseGateway`-Implementierung auf httpx-Basis.

    Verantwortlich für:
    - Bearer-Token-Verwaltung inkl. Auto-Relogin bei HTTP 401
    - Rate-Limiting (Ban-Schutz)
    - Fehler-Mapping HTTP → Domain-Exceptions
    """

    def __init__(
        self,
        config: KickbaseClientConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config or KickbaseClientConfig()
        self._session: Session | None = None
        self._credentials: tuple[str, str] | None = None
        self._limiter = AsyncRateLimiter(
            max_calls=self._config.max_requests_per_minute,
            jitter=(self._config.jitter_min_s, self._config.jitter_max_s),
        )
        self._http = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.request_timeout_s,
            headers={"User-Agent": self._config.user_agent, "Accept": "application/json"},
            transport=transport,
        )

    # -- Lifecycle -----------------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> HttpxKickbaseClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- Auth ----------------------------------------------------------

    async def login(self, email: str, password: str) -> Session:
        payload = {"email": email, "password": password, "ext": False}
        response = await self._request("POST", "/v4/user/login", json=payload, authed=False)
        session = LoginResponseDTO.model_validate(response).to_session()
        self._session = session
        self._credentials = (email, password)
        return session

    # -- Ligen ---------------------------------------------------------

    async def list_leagues(self) -> list[League]:
        data = await self._request("GET", "/v4/leagues/selection")
        return [dto.to_domain() for dto in LeagueSelectionDTO.model_validate(data).it]

    # -- Squad + Markt -------------------------------------------------

    async def get_squad(self, league_id: str, manager_id: str) -> Squad:
        path = f"/v4/leagues/{league_id}/managers/{manager_id}/squad"
        data = await self._request("GET", path)
        return SquadResponseDTO.model_validate(data).to_domain(league_id, manager_id)

    async def get_market(self, league_id: str) -> list[MarketPlayer]:
        data = await self._request("GET", f"/v4/leagues/{league_id}/market")
        return [m.to_market_player() for m in MarketResponseDTO.model_validate(data).it]

    async def place_bid(self, league_id: str, player_id: str, price: Decimal) -> str:
        path = f"/v4/leagues/{league_id}/market/{player_id}/offers"
        data = await self._request("POST", path, json={"price": int(price)})
        return BidResponseDTO.model_validate(data).id

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        path = f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/accept"
        await self._request("POST", path)

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        path = f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/decline"
        await self._request("POST", path)

    # -- Spieltage + Marktwert-Historie --------------------------------

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]:
        data = await self._request("GET", f"/v4/competitions/{competition_id}/matchdays")
        return [m.to_domain() for m in MatchdaysResponseDTO.model_validate(data).it]

    async def get_market_value_history(
        self, league_id: str, player_id: str, days: int = 7
    ) -> list[MarketValuePoint]:
        path = f"/v4/leagues/{league_id}/players/{player_id}/marketvalue/{days}"
        data = await self._request("GET", path)
        return [p.to_domain() for p in MarketValueResponseDTO.model_validate(data).it]

    # -- Interner Request-Kern -----------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        authed: bool = True,
        _attempt: int = 0,
    ) -> dict[str, Any]:
        await self._limiter.acquire()

        headers: dict[str, str] = {}
        if authed:
            if self._session is None:
                raise AuthError("Nicht eingeloggt — login() zuerst aufrufen.")
            headers["Authorization"] = f"Bearer {self._session.token}"

        try:
            response = await self._http.request(method, path, json=json, headers=headers)
        except httpx.RequestError as exc:
            raise TransportError(f"Netzwerkfehler bei {method} {path}: {exc}") from exc

        if (
            response.status_code == HTTPStatus.UNAUTHORIZED
            and authed
            and _attempt < self._config.max_relogin_attempts
        ):
            _log.info("401 bei %s %s — versuche Relogin", method, path)
            await self._relogin()
            return await self._request(
                method, path, json=json, authed=authed, _attempt=_attempt + 1
            )

        self._raise_for_status(response, method, path)

        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise TransportError(f"Ungültige JSON-Antwort von {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise TransportError(f"Erwartetes JSON-Objekt von {path}, bekam: {type(data).__name__}")
        return data

    async def _relogin(self) -> None:
        if self._credentials is None:
            raise AuthError("Session abgelaufen, aber keine Credentials für Relogin vorhanden.")
        email, password = self._credentials
        self._session = None
        await self.login(email, password)

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
        code = response.status_code
        if code < HTTPStatus.BAD_REQUEST:
            return
        detail = _safe_error_message(response)
        msg = f"{method} {path} → {code}: {detail}"
        if code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            raise AuthError(msg)
        if code == HTTPStatus.NOT_FOUND:
            raise NotFoundError(msg)
        if code == HTTPStatus.CONFLICT:
            raise ConflictError(msg)
        if code == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimitError(msg)
        if HTTPStatus.INTERNAL_SERVER_ERROR <= code < _SERVER_ERROR_CEILING:
            raise TransportError(msg)
        raise KickbaseError(msg)


_SERVER_ERROR_CEILING = 600


def _safe_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("err") or body)[:200]
    return str(body)[:200]
