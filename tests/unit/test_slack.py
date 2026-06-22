"""HttpSlackClient — chat.postMessage happy + error paths."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from hyejin_bot.core.errors import AuthError, TransientError
from hyejin_bot.infra.slack import FakeSlackClient, HttpSlackClient


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpSlackClient:
    transport = httpx.MockTransport(handler)
    return HttpSlackClient(
        bot_token="xoxb-test",
        http_client=httpx.AsyncClient(transport=transport, timeout=5.0),
    )


@pytest.mark.asyncio
async def test_post_message_happy_path_sends_expected_payload() -> None:
    seen: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization", "")
        seen["body"] = req.content.decode()
        return httpx.Response(200, json={"ok": True, "channel": "D08GP012483", "ts": "1.2"})

    await _client(_handler).post_message(channel="D08GP012483", text="hi")
    assert seen["url"] == "https://slack.com/api/chat.postMessage"
    assert seen["auth"] == "Bearer xoxb-test"
    # httpx packs JSON with compact separators (no space after colon).
    body = seen["body"]
    assert isinstance(body, str)
    assert '"channel":"D08GP012483"' in body
    assert '"text":"hi"' in body


@pytest.mark.asyncio
async def test_invalid_auth_maps_to_auth_error() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    with pytest.raises(AuthError, match="invalid_auth"):
        await _client(_handler).post_message(channel="D08GP012483", text="hi")


@pytest.mark.asyncio
async def test_token_revoked_maps_to_auth_error() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "token_revoked"})

    with pytest.raises(AuthError, match="token_revoked"):
        await _client(_handler).post_message(channel="D08GP012483", text="hi")


@pytest.mark.asyncio
async def test_rate_limited_maps_to_transient_error() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "rate_limited"})

    with pytest.raises(TransientError, match="rate_limited"):
        await _client(_handler).post_message(channel="D08GP012483", text="hi")


@pytest.mark.asyncio
async def test_http_500_maps_to_transient_error() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream broken")

    with pytest.raises(TransientError):
        await _client(_handler).post_message(channel="D08GP012483", text="hi")


@pytest.mark.asyncio
async def test_non_json_response_maps_to_transient_error() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nope</html>")

    with pytest.raises(TransientError, match="non-json"):
        await _client(_handler).post_message(channel="D08GP012483", text="hi")


@pytest.mark.asyncio
async def test_transport_error_maps_to_transient_error() -> None:
    def _handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    with pytest.raises(TransientError, match="transport"):
        await _client(_handler).post_message(channel="D08GP012483", text="hi")


@pytest.mark.asyncio
async def test_fake_slack_client_records_calls() -> None:
    client = FakeSlackClient()
    await client.post_message(channel="D1", text="a")
    await client.post_message(channel="D2", text="b")
    assert client.calls == [
        {"channel": "D1", "text": "a"},
        {"channel": "D2", "text": "b"},
    ]


@pytest.mark.asyncio
async def test_fake_slack_client_raises_when_configured() -> None:
    client = FakeSlackClient(raise_on_post=AuthError("nope"))
    with pytest.raises(AuthError):
        await client.post_message(channel="D1", text="a")
