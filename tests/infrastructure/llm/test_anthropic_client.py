from __future__ import annotations

import httpx
import pytest
from app.infrastructure.llm.anthropic_client import AnthropicClient, LlmVerificationError


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
