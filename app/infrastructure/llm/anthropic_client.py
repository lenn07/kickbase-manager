"""Anthropic-Adapter — für Setup-Zweck reicht ein Verifikations-Ping.

Wir gehen bewusst über httpx statt SDK-Client, damit
- der Test synchron isoliert testbar ist (MockTransport),
- keine Import-Reihenfolge-Probleme mit dem `anthropic`-Package entstehen,
- der Setup-Pfad minimal-invasiv bleibt.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Protocol

import httpx


class LlmVerificationError(Exception):
    """Anthropic-Key konnte nicht verifiziert werden."""


class LlmGateway(Protocol):
    async def verify_key(self, api_key: str) -> None: ...


DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicClient:
    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._timeout_s = timeout_s
        self._transport = transport

    async def verify_key(self, api_key: str) -> None:
        """Sendet einen Minimalprompt; wirft LlmVerificationError bei Fehler."""
        if not api_key or not api_key.strip():
            raise LlmVerificationError("API-Key ist leer.")

        headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "ping"}],
        }

        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as http:
            try:
                response = await http.post(self._endpoint, json=payload, headers=headers)
            except httpx.RequestError as exc:
                raise LlmVerificationError(f"Netzwerkfehler beim Anthropic-Test: {exc}") from exc

        if response.status_code == HTTPStatus.OK:
            return

        raise LlmVerificationError(_format_error(response))


def _format_error(response: httpx.Response) -> str:
    detail: str
    try:
        body = response.json()
    except ValueError:
        detail = response.text[:200]
    else:
        detail = (body.get("error", {}).get("message") if isinstance(body, dict) else None) or str(
            body
        )[:200]
    return f"Anthropic HTTP {response.status_code}: {detail}"
