"""Anthropic-Adapter — Setup-Verifikation + Kurator-Aufruf via Tool-Use.

Zwei Aufgaben, ein HTTP-Client:

1. `verify_key` — Minimaler Ping (Haiku, `max_tokens=10`) im Setup (F-1c).
2. `select_action` — Kurator-Call (Sonnet, Tool-Use mit forced `tool_choice`)
   erzwingt strukturierten JSON-Output; der `LlmCurator` bekommt garantiert
   ein Dict mit `candidate_id` + `reason` zurück (oder eine domain-spezifische
   Fehler-Exception).

Wir gehen über `httpx` statt SDK-Client, damit
- Tests synchron isoliert mit `MockTransport` fahrbar sind,
- keine Import-Reihenfolge-Probleme mit dem `anthropic`-Package entstehen,
- der Setup-Pfad minimal-invasiv bleibt.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Protocol

import httpx


class LlmVerificationError(Exception):
    """Anthropic-Key konnte nicht verifiziert werden."""


class LlmChatError(Exception):
    """Kurator-Call an Anthropic gescheitert (Netz, 4xx/5xx, ungültige Struktur)."""


class LlmGateway(Protocol):
    async def verify_key(self, api_key: str) -> None: ...


class LlmChatGateway(Protocol):
    """Runtime-Port: gibt dem LLM ein Tool-Schema und liefert das gewählte Input-Dict."""

    async def select_action(
        self,
        *,
        api_key: str,
        system_prompt: str,
        user_message: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 512,
    ) -> dict[str, Any]: ...


DEFAULT_VERIFY_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_CURATOR_MODEL = "claude-sonnet-4-6"
DEFAULT_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicClient:
    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        verify_model: str = DEFAULT_VERIFY_MODEL,
        curator_model: str = DEFAULT_CURATOR_MODEL,
        timeout_s: float = 15.0,
        curator_timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._verify_model = verify_model
        self._curator_model = curator_model
        self._timeout_s = timeout_s
        # Kurator-Antworten (Sonnet, bis 512 Tokens) brauchen mehr Zeit als der
        # Ping — mit dem 15-s-Verify-Timeout würden bereits normale Latenzen
        # den Tick abbrechen.
        self._curator_timeout_s = curator_timeout_s
        self._transport = transport

    async def verify_key(self, api_key: str) -> None:
        """Sendet einen Minimalprompt; wirft LlmVerificationError bei Fehler."""
        if not api_key or not api_key.strip():
            raise LlmVerificationError("API-Key ist leer.")

        payload = {
            "model": self._verify_model,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "ping"}],
        }
        try:
            response = await self._post(api_key, payload, timeout_s=self._timeout_s)
        except httpx.RequestError as exc:
            raise LlmVerificationError(f"Netzwerkfehler beim Anthropic-Test: {exc}") from exc

        if response.status_code == HTTPStatus.OK:
            return
        raise LlmVerificationError(_format_error(response))

    async def select_action(
        self,
        *,
        api_key: str,
        system_prompt: str,
        user_message: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        if not api_key or not api_key.strip():
            raise LlmChatError("API-Key ist leer.")

        payload: dict[str, Any] = {
            "model": self._curator_model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
            "tools": [
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        try:
            response = await self._post(api_key, payload, timeout_s=self._curator_timeout_s)
        except httpx.TimeoutException as exc:
            raise LlmChatError(
                f"Anthropic-Call Timeout nach {self._curator_timeout_s:.0f}s: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise LlmChatError(f"Netzwerkfehler beim Anthropic-Call: {exc}") from exc

        if response.status_code != HTTPStatus.OK:
            raise LlmChatError(_format_error(response))

        return _extract_tool_input(response, tool_name)

    async def _post(
        self, api_key: str, payload: dict[str, Any], *, timeout_s: float
    ) -> httpx.Response:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout_s, transport=self._transport) as http:
            return await http.post(self._endpoint, json=payload, headers=headers)


def _extract_tool_input(response: httpx.Response, tool_name: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise LlmChatError(f"Anthropic lieferte kein JSON: {response.text[:200]}") from exc

    if not isinstance(body, dict):
        raise LlmChatError(f"Anthropic-Response kein Objekt: {type(body).__name__}")

    content = body.get("content")
    if not isinstance(content, list):
        raise LlmChatError("Anthropic-Response ohne `content`-Liste.")

    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == tool_name
        ):
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                return tool_input
            raise LlmChatError("Tool-Use-Block ohne dict-`input`.")

    raise LlmChatError(f"Kein Tool-Use-Block für `{tool_name}` im Response.")


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
