"""Async-HTTP-Adapter für Kickbase v4 — implementiert `KickbaseGateway`."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
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
from app.domain.gateways import SessionStore
from app.domain.models import (
    League,
    LeagueMe,
    MarketPlayer,
    MarketValuePoint,
    Matchday,
    Session,
    Squad,
)
from app.infrastructure.kickbase.config import KickbaseClientConfig
from app.infrastructure.kickbase.dto import (
    BidResponseDTO,
    LeagueMeDTO,
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
        session_store: SessionStore | None = None,
    ) -> None:
        self._config = config or KickbaseClientConfig()
        self._session: Session | None = None
        self._credentials: tuple[str, str] | None = None
        self._session_store = session_store
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
        payload = {"em": email, "pass": password}
        response = await self._request("POST", "/v4/user/login", json=payload, authed=False)
        session = LoginResponseDTO.model_validate(response).to_session()
        self._session = session
        self._credentials = (email, password)
        if self._session_store is not None:
            await self._session_store.save_session(session)
        return session

    # -- Ligen ---------------------------------------------------------

    async def list_leagues(self) -> list[League]:
        data = await self._request("GET", "/v4/leagues/selection")
        return [dto.to_domain() for dto in LeagueSelectionDTO.model_validate(data).it]

    async def get_league_me(self, league_id: str) -> LeagueMe:
        data = await self._request("GET", f"/v4/leagues/{league_id}/me")
        return LeagueMeDTO.model_validate(data).to_domain(league_id)

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

    async def list_on_market(self, league_id: str, player_id: str, price: Decimal) -> str:
        # v4 legt ein Verkaufs-Listing über POST /leagues/{lid}/market/ an
        # (playerId + price im Body). Der Trailing Slash ist Pflicht — sonst
        # antwortet Kickbase mit HTTP 500. Response ist ein leeres Objekt {}.
        path = f"/v4/leagues/{league_id}/market/"
        await self._request("POST", path, json={"playerId": player_id, "price": int(price)})
        return player_id

    async def sell_to_kickbase(self, league_id: str, player_id: str) -> None:
        # v4: DELETE /leagues/{lid}/market/{pid}/sell nimmt das automatische
        # Kickbase-Angebot in Marktwert-Höhe an. Voraussetzung: Spieler ist
        # bereits gelistet.
        path = f"/v4/leagues/{league_id}/market/{player_id}/sell"
        await self._request("DELETE", path)

    async def remove_from_market(self, league_id: str, player_id: str) -> None:
        # v4: DELETE /leagues/{lid}/market/{pid} zieht ein laufendes Listing
        # zurück, ohne den Spieler zu verkaufen.
        path = f"/v4/leagues/{league_id}/market/{player_id}"
        await self._request("DELETE", path)

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        path = f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/accept"
        await self._request("POST", path)

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str) -> None:
        path = f"/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/decline"
        await self._request("POST", path)

    # -- Spieltage + Marktwert-Historie --------------------------------

    async def list_matchdays(self, competition_id: str = "1") -> list[Matchday]:
        data = await self._request("GET", f"/v4/competitions/{competition_id}/matchdays")
        return MatchdaysResponseDTO.model_validate(data).to_domain()

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
        _retry_5xx: int = 0,
    ) -> dict[str, Any]:
        await self._limiter.acquire()

        headers: dict[str, str] = {}
        if authed:
            await self._ensure_session()
            assert self._session is not None  # von _ensure_session garantiert
            headers["Authorization"] = f"Bearer {self._session.token}"

        try:
            response = await self._http.request(method, path, json=json, headers=headers)
        except httpx.RequestError as exc:
            # Netzwerkfehler zählen wie 5xx: transient, retry lohnt sich.
            if _retry_5xx < self._config.max_retries_5xx:
                await self._backoff_sleep(_retry_5xx)
                return await self._request(
                    method,
                    path,
                    json=json,
                    authed=authed,
                    _attempt=_attempt,
                    _retry_5xx=_retry_5xx + 1,
                )
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

        if (
            HTTPStatus.INTERNAL_SERVER_ERROR <= response.status_code < _SERVER_ERROR_CEILING
            and _retry_5xx < self._config.max_retries_5xx
        ):
            _log.info(
                "5xx (%d) bei %s %s — Retry %d/%d",
                response.status_code,
                method,
                path,
                _retry_5xx + 1,
                self._config.max_retries_5xx,
            )
            await self._backoff_sleep(_retry_5xx)
            return await self._request(
                method,
                path,
                json=json,
                authed=authed,
                _attempt=_attempt,
                _retry_5xx=_retry_5xx + 1,
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

    async def _backoff_sleep(self, attempt: int) -> None:
        base = self._config.backoff_base_s * (2**attempt)
        jitter = random.uniform(0.0, self._config.backoff_base_s)  # noqa: S311 — nur Jitter
        await asyncio.sleep(base + jitter)

    async def _ensure_session(self) -> None:
        if self._session is not None and not self._is_session_expired(self._session):
            return

        if self._session_store is not None:
            cached = await self._session_store.load_session()
            if cached is not None and not self._is_session_expired(cached):
                self._session = cached
                return

        self._session = None
        await self._relogin()

    async def _relogin(self) -> None:
        credentials = self._credentials
        if credentials is None and self._session_store is not None:
            credentials = await self._session_store.load_credentials()
        if credentials is None:
            raise AuthError("Session abgelaufen, aber keine Credentials für Relogin vorhanden.")
        email, password = credentials
        self._session = None
        await self.login(email, password)

    def _is_session_expired(self, session: Session) -> bool:
        margin = timedelta(seconds=self._config.session_expiry_margin_s)
        return datetime.now(UTC) + margin >= session.token_expires_at

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
