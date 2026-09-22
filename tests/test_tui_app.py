"""Headless tests for the TUI shell and the login flow.

Textual's own ``run_test`` pilot drives the real widget tree, so these tests
cover the wiring between screen, worker and client rather than mocking it away.
``asyncio.run`` is used instead of ``pytest-asyncio`` to keep the dev
dependency set unchanged.
"""

from __future__ import annotations

import asyncio

import httpx

from apps.tui.app import PaperFactoryTui
from apps.tui.client import ControlPlaneClient
from apps.tui.screens.home import HomeScreen
from apps.tui.screens.login import LoginScreen
from textual.widgets import Input

BASE_URL = "http://127.0.0.1:8000"

LOGIN_OK = {
    "access_token": "token-abc",
    "token_type": "bearer",
    "username": "alice",
    "role": "admin",
    "status": "active",
}


def _app(handler) -> PaperFactoryTui:
    client = ControlPlaneClient(BASE_URL, transport=httpx.MockTransport(handler))
    return PaperFactoryTui(BASE_URL, client=client)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=LOGIN_OK)


def _reject(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"detail": "用户名或密码错误"})


def test_app_starts_on_the_login_screen() -> None:
    async def scenario() -> None:
        app = _app(_ok)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen)
            assert app.session is None

    asyncio.run(scenario())


def test_successful_login_opens_home_with_session() -> None:
    async def scenario() -> None:
        app = _app(_ok)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#username", Input).value = "alice"
            app.screen.query_one("#password", Input).value = "secret"
            await pilot.click("#login")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.session is not None
            assert app.session.username == "alice"
            assert app.session.is_admin is True
            assert isinstance(app.screen, HomeScreen)

    asyncio.run(scenario())


def test_rejected_credentials_keep_user_on_login() -> None:
    async def scenario() -> None:
        app = _app(_reject)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#username", Input).value = "alice"
            app.screen.query_one("#password", Input).value = "wrong"
            await pilot.click("#login")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.session is None
            assert isinstance(app.screen, LoginScreen)
            assert app.screen.last_error
            # The message classifies the failure without echoing the password.
            assert "wrong" not in app.screen.last_error

    asyncio.run(scenario())


def test_unreachable_backend_is_reported_on_screen() -> None:
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def scenario() -> None:
        app = _app(dead)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#username", Input).value = "alice"
            app.screen.query_one("#password", Input).value = "secret"
            await pilot.click("#login")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.session is None
            assert isinstance(app.screen, LoginScreen)
            assert BASE_URL in app.screen.last_error

    asyncio.run(scenario())


def test_empty_credentials_short_circuit_without_a_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=LOGIN_OK)

    async def scenario() -> None:
        app = _app(handler)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#login")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.session is None
            assert app.screen.last_error == "请输入用户名和密码"

    asyncio.run(scenario())
    assert calls == [], "empty credentials must not reach the backend"


def test_quit_at_login_exits_without_a_session() -> None:
    async def scenario() -> None:
        app = _app(_ok)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#quit")
            await pilot.pause()
            assert app.session is None

    asyncio.run(scenario())


def test_escape_at_login_exits_without_a_session() -> None:
    async def scenario() -> None:
        app = _app(_ok)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.session is None

    asyncio.run(scenario())
