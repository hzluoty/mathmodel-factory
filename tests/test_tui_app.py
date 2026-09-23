"""Headless tests for the TUI shell and the login flow.

Textual's own ``run_test`` pilot drives the real widget tree, so these tests
cover the wiring between screen, worker and client rather than mocking it away.
``asyncio.run`` is used instead of ``pytest-asyncio`` to keep the dev
dependency set unchanged.

The realtime connector is injected so no test here opens a socket; the live
WebSocket path is covered in ``tests/test_tui_realtime.py``.

Waits are bounded condition polls rather than ``workers.wait_for_complete``:
the realtime feed is *designed* to stay parked until stopped, so waiting for
all workers to finish would deadlock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from textual.widgets import DataTable, Input

from apps.tui.app import PaperFactoryTui
from apps.tui.client import ControlPlaneClient
from apps.tui.realtime import DISCONNECTED
from apps.tui.screens.login import LoginScreen
from apps.tui.screens.projects import ProjectsScreen

BASE_URL = "http://127.0.0.1:8000"

LOGIN_OK = {
    "access_token": "token-abc",
    "token_type": "bearer",
    "username": "alice",
    "role": "admin",
    "status": "active",
}


def _handler(*, reject: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            if reject:
                return httpx.Response(401, json={"detail": "用户名或密码错误"})
            return httpx.Response(200, json=LOGIN_OK)
        if request.url.path == "/api/auth/ws-ticket":
            return httpx.Response(200, json={"ticket": "t-1"})
        return httpx.Response(404, json={"detail": "not found"})

    return handler


def _dead_end(request: httpx.Request) -> httpx.Response:
    """The backend is not listening: every HTTP call fails."""

    raise httpx.ConnectError("connection refused")


class _RaisingConnector:
    """Stands in for websockets.connect when there is no reachable socket."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or OSError("no websocket backend")

    def __call__(self, _url: str) -> _RaisingConnector:
        return self

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def _park(_delay: float) -> None:
    """A sleep that never returns, so the feed cannot spin inside a test."""

    await asyncio.Event().wait()


def _app(handler, *, connector=None) -> PaperFactoryTui:
    client = ControlPlaneClient(BASE_URL, transport=httpx.MockTransport(handler))
    return PaperFactoryTui(
        BASE_URL,
        client=client,
        realtime_connector=connector or _RaisingConnector(),
        realtime_sleep=_park,
    )


def _fill(screen, username: str, password: str) -> None:
    screen.query_one("#username", Input).value = username
    screen.query_one("#password", Input).value = password


async def _settle(pilot, predicate: Callable[[], bool], attempts: int = 60) -> bool:
    """Yield to the event loop until ``predicate`` holds, or give up."""

    for _ in range(attempts):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


def test_app_starts_on_the_login_screen() -> None:
    async def scenario() -> None:
        app = _app(_handler())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen)
            assert app.session is None

    asyncio.run(scenario())


def test_successful_login_opens_the_project_screen() -> None:
    async def scenario() -> None:
        app = _app(_handler())
        async with app.run_test() as pilot:
            await pilot.pause()
            _fill(app.screen, "alice", "secret")
            await pilot.click("#login")
            settled = await _settle(pilot, lambda: isinstance(app.screen, ProjectsScreen))

            assert settled, "login never reached the project screen"
            assert app.session is not None
            assert app.session.username == "alice"
            assert app.session.is_admin is True
            assert app.screen.query_one("#projects", DataTable) is not None

    asyncio.run(scenario())


def test_rejected_credentials_keep_user_on_login() -> None:
    async def scenario() -> None:
        app = _app(_handler(reject=True))
        async with app.run_test() as pilot:
            await pilot.pause()
            _fill(app.screen, "alice", "wrong")
            await pilot.click("#login")
            await _settle(pilot, lambda: bool(getattr(app.screen, "last_error", "")))

            assert app.session is None
            assert isinstance(app.screen, LoginScreen)
            assert app.screen.last_error
            # The message classifies the failure without echoing the password.
            assert "wrong" not in app.screen.last_error

    asyncio.run(scenario())


def test_unreachable_backend_is_reported_on_screen() -> None:
    async def scenario() -> None:
        app = _app(_dead_end)
        async with app.run_test() as pilot:
            await pilot.pause()
            _fill(app.screen, "alice", "secret")
            await pilot.click("#login")
            await _settle(pilot, lambda: bool(getattr(app.screen, "last_error", "")))

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
            await _settle(pilot, lambda: bool(getattr(app.screen, "last_error", "")))

            assert app.session is None
            assert app.screen.last_error == "请输入用户名和密码"

    asyncio.run(scenario())
    assert calls == [], "empty credentials must not reach the backend"


def test_quit_at_login_exits_without_a_session() -> None:
    async def scenario() -> None:
        app = _app(_handler())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#quit")
            await pilot.pause()
            assert app.session is None

    asyncio.run(scenario())


def test_escape_at_login_exits_without_a_session() -> None:
    async def scenario() -> None:
        app = _app(_handler())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.session is None

    asyncio.run(scenario())


def test_dead_realtime_socket_is_surfaced_not_swallowed() -> None:
    """The M0 placeholder would have hidden this; the feed must report it."""

    async def scenario() -> tuple[str, str]:
        app = _app(_handler())
        async with app.run_test() as pilot:
            await pilot.pause()
            _fill(app.screen, "alice", "secret")
            await pilot.click("#login")
            settled = await _settle(
                pilot,
                lambda: getattr(app.screen, "feed_state", ("", ""))[0] == DISCONNECTED,
            )
            assert settled, "feed never reported a dead socket"
            assert isinstance(app.screen, ProjectsScreen)
            return app.screen.feed_state

    kind, detail = asyncio.run(scenario())
    assert kind == DISCONNECTED
    assert "no websocket backend" in detail
