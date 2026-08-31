from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.infrastructure.llm.anthropic_client import (
    AnthropicClient,
    LlmChatError,
    LlmVerificationError,
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["candidate_id", "reason"],
}


async def test_verify_key_success_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sk-ant-good"
        assert request.headers["anthropic-version"]
        return httpx.Response(200, json={"content": [{"text": "pong"}]})

    client = AnthropicClient(transport=httpx.MockTransport(handler))
    await client.verify_key("sk-ant-good")


async def test_verify_key_rejects_empty_key() -> None:
    client = AnthropicClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    with pytest.raises(LlmVerificationError):
        await client.verify_key("   ")


async def test_verify_key_maps_401_to_domain_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    client = AnthropicClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LlmVerificationError) as exc:
        await client.verify_key("sk-ant-bad")
    assert "invalid api key" in str(exc.value).lower()


async def test_verify_key_maps_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    client = AnthropicClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LlmVerificationError) as exc:
        await client.verify_key("sk-ant-good")
    assert "netzwerkfehler" in str(exc.value).lower()


async def _call_select_action(
    handler: httpx.MockTransport | None = None,
    *,
    api_key: str = "sk-ant-good",
    transport: httpx.MockTransport | None = None,
) -> dict[str, Any]:
    client = AnthropicClient(transport=transport or handler)
    return await client.select_action(
        api_key=api_key,
        system_prompt="sys",
        user_message="pick",
        tool_name="select_action",
        tool_description="wähle",
        input_schema=_SCHEMA,
    )


async def test_select_action_returns_tool_use_input() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        assert request.headers["x-api-key"] == "sk-ant-good"
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "select_action",
                        "input": {"candidate_id": "BUY:m1", "reason": "top form"},
                    }
                ],
                "stop_reason": "tool_use",
            },
        )

    result = await _call_select_action(transport=httpx.MockTransport(handler))
    assert result == {"candidate_id": "BUY:m1", "reason": "top form"}
    assert b'"tool_choice"' in captured["body"]
    assert b"select_action" in captured["body"]


async def test_select_action_maps_http_error_to_chat_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with pytest.raises(LlmChatError) as exc:
        await _call_select_action(transport=httpx.MockTransport(handler))
    assert "rate limited" in str(exc.value).lower()


async def test_select_action_maps_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(LlmChatError) as exc:
        await _call_select_action(transport=httpx.MockTransport(handler))
    assert "netzwerkfehler" in str(exc.value).lower()


async def test_select_action_without_tool_use_block_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "hi"}]},
        )

    with pytest.raises(LlmChatError) as exc:
        await _call_select_action(transport=httpx.MockTransport(handler))
    assert "tool" in str(exc.value).lower()


async def test_select_action_empty_key_raises_without_http_call() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP-Call sollte nicht passieren")

    with pytest.raises(LlmChatError):
        await _call_select_action(transport=httpx.MockTransport(handler), api_key="   ")


async def test_select_action_bad_json_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    with pytest.raises(LlmChatError):
        await _call_select_action(transport=httpx.MockTransport(handler))


async def test_select_action_timeout_is_mapped_to_chat_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(LlmChatError) as exc:
        await _call_select_action(transport=httpx.MockTransport(handler))
    assert "timeout" in str(exc.value).lower()


async def test_curator_timeout_can_be_configured_larger_than_verify_timeout() -> None:
    """Verify- und Kurator-Timeout müssen separat konfiguriert werden können —
    ohne Sonderpfad würde der Sonnet-Call am 15-s-Verify-Timeout scheitern."""
    seen_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx propagiert den AsyncClient-Timeout in die Request-Extensions.
        timeout_config = request.extensions.get("timeout", {})
        seen_timeouts.append(timeout_config.get("read"))
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "select_action",
                        "input": {"candidate_id": "HOLD:0", "reason": "x"},
                    }
                ]
            },
        )

    client = AnthropicClient(
        transport=httpx.MockTransport(handler),
        timeout_s=5.0,
        curator_timeout_s=30.0,
    )
    await client.select_action(
        api_key="sk-ant-good",
        system_prompt="s",
        user_message="m",
        tool_name="select_action",
        tool_description="d",
        input_schema=_SCHEMA,
    )
    await client.verify_key("sk-ant-good")

    assert seen_timeouts == [30.0, 5.0]
