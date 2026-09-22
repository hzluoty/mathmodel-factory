"""Contract tests for the control-plane client.

A ``httpx.MockTransport`` stands in for ``web/backend`` so these tests pin the
client's behaviour against recorded response shapes without a live server.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from apps.tui.client import (
    AuthError,
    ConnectionFailed,
    ControlPlaneClient,
    ControlPlaneError,
    ForbiddenError,
)
from apps.tui.contracts import Session, normalize_session

BASE_URL = "http://127.0.0.1:8000"


def _client(handler) -> ControlPlaneClient:
    return ControlPlaneClient(BASE_URL, transport=httpx.MockTransport(handler))


def _json_handler(status: int, payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


LOGIN_OK = {
    "access_token": "token-abc",
    "token_type": "bearer",
    "username": "alice",
    "role": "admin",
    "status": "active",
}


def test_login_returns_session_and_retains_it() -> None:
    async def scenario() -> None:
        client = _client(_json_handler(200, LOGIN_OK))
        session = await client.login("alice", "secret")
        assert isinstance(session, Session)
        assert session.access_token == "token-abc"
        assert session.username == "alice"
        assert session.is_admin is True
        assert client.is_authenticated is True
        assert client.session is session
        await client.aclose()

    asyncio.run(scenario())


def test_login_sends_bearer_token_on_later_requests() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=LOGIN_OK)
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"ticket": "t-1"})

    async def scenario() -> None:
        client = _client(handler)
        await client.login("alice", "secret")
        await client.ws_ticket()
        await client.aclose()

    asyncio.run(scenario())
    assert seen == ["Bearer token-abc"]


def test_rejected_credentials_raise_auth_error() -> None:
    async def scenario() -> None:
        client = _client(_json_handler(401, {"detail": "用户名或密码错误"}))
        with pytest.raises(AuthError):
            await client.login("alice", "wrong")
        assert client.is_authenticated is False
        await client.aclose()

    asyncio.run(scenario())


def test_missing_grant_raises_forbidden() -> None:
    async def scenario() -> None:
        client = _client(_json_handler(403, {"detail": "forbidden"}))
        with pytest.raises(ForbiddenError):
            await client.login("alice", "secret")
        await client.aclose()

    asyncio.run(scenario())


def test_server_error_surfaces_backend_detail() -> None:
    async def scenario() -> None:
        client = _client(_json_handler(500, {"detail": "数据库暂时不可用"}))
        with pytest.raises(ControlPlaneError) as caught:
            await client.login("alice", "secret")
        assert "数据库暂时不可用" in str(caught.value)
        await client.aclose()

    asyncio.run(scenario())


def test_unreachable_backend_raises_connection_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def scenario() -> None:
        client = _client(handler)
        with pytest.raises(ConnectionFailed) as caught:
            await client.login("alice", "secret")
        assert BASE_URL in str(caught.value)
        await client.aclose()

    asyncio.run(scenario())


def test_timeout_is_reported_as_timeout_not_generic_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    async def scenario() -> None:
        client = _client(handler)
        with pytest.raises(ConnectionFailed) as caught:
            await client.login("alice", "secret")
        assert "超时" in str(caught.value)
        await client.aclose()

    asyncio.run(scenario())


def test_ws_ticket_requires_login_first() -> None:
    async def scenario() -> None:
        client = _client(_json_handler(200, {"ticket": "t-1"}))
        with pytest.raises(AuthError):
            await client.ws_ticket()
        await client.aclose()

    asyncio.run(scenario())


def test_ws_ticket_missing_field_is_a_contract_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=LOGIN_OK)
        return httpx.Response(200, json={})

    async def scenario() -> None:
        client = _client(handler)
        await client.login("alice", "secret")
        with pytest.raises(ControlPlaneError):
            await client.ws_ticket()
        await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:8000", "ws://127.0.0.1:8000/ws?ticket=t-1"),
        ("https://factory.example.com", "wss://factory.example.com/ws?ticket=t-1"),
        ("http://127.0.0.1:8000/", "ws://127.0.0.1:8000/ws?ticket=t-1"),
    ],
)
def test_websocket_url_matches_backend_scheme(base_url: str, expected: str) -> None:
    client = ControlPlaneClient(base_url, transport=httpx.MockTransport(_json_handler(200, {})))
    assert client.websocket_url("t-1") == expected


def test_logout_clears_local_session_even_if_backend_call_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=LOGIN_OK)
        raise httpx.ConnectError("gone")

    async def scenario() -> None:
        client = _client(handler)
        await client.login("alice", "secret")
        assert client.is_authenticated is True
        await client.logout()
        assert client.is_authenticated is False
        await client.aclose()

    asyncio.run(scenario())


def test_normalize_session_rejects_payload_without_token() -> None:
    with pytest.raises(ValueError):
        normalize_session({"username": "alice"})
    with pytest.raises(ValueError):
        normalize_session("not-a-dict")


def test_normalize_session_defaults_optional_fields() -> None:
    session = normalize_session({"access_token": "t"})
    assert session.role == "user"
    assert session.status == "active"
    assert session.is_admin is False
