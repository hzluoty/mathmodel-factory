"""Realtime feed tests.

Two layers, deliberately:

* The delivery path is exercised against a **real local WebSocket server**, so
  the ticket handshake, URL construction and frame decoding are covered by an
  actual socket rather than a mock of one.
* Reconnect and backoff are driven by a **scripted connector** and an injected
  sleep, so the retry schedule is asserted without waiting for wall-clock time
  and without a hot retry loop.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from websockets.asyncio.server import serve

from apps.tui.client import ControlPlaneClient
from apps.tui.contracts import Session, normalize_project
from apps.tui.realtime import CONNECTING, DISCONNECTED, LIVE, RealtimeFeed

TICKET = "ticket-xyz"


def _status(base_name: str, **overrides) -> dict:
    payload = {
        "base_name": base_name,
        "status": "running",
        "display_status": "运行中",
        "current_step": 3,
        "total_steps": 16,
        "progress_percent": 18.75,
        "is_running": True,
        "last_updated": "2026-09-22T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _logged_in(client: ControlPlaneClient) -> ControlPlaneClient:
    """Give the client a session, so the feed may mint tickets without a login."""

    client._session = Session(access_token="t", username="alice")
    return client


def _client(base_url: str, tickets: list[str] | None = None) -> ControlPlaneClient:
    """A client whose HTTP side is faked but whose WS side is real."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/ws-ticket":
            if tickets is not None:
                tickets.append(TICKET)
            return httpx.Response(200, json={"ticket": TICKET})
        return httpx.Response(404, json={"detail": "not found"})

    return _logged_in(
        ControlPlaneClient(base_url, transport=httpx.MockTransport(handler))
    )


class _Stop(Exception):
    """Raised by the injected sleep to end the reconnect loop under test."""


class _FailingConn:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _EmptyConn:
    """A connection that opens, yields nothing, and closes cleanly."""

    async def __aenter__(self) -> "_EmptyConn":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> "_EmptyConn":
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self) -> None:
        return None


class _ScriptedConnector:
    """Opens or fails according to a script; records every attempted URL."""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        kind = self.script.pop(0) if self.script else "fail"
        if kind == "live":
            return _EmptyConn()
        return _FailingConn(OSError("socket refused"))


def test_feed_delivers_snapshots_from_a_real_socket() -> None:
    """End-to-end over a real socket: ticket, URL, frames, normalisation."""

    async def scenario() -> None:
        paths: list[str] = []
        frames = [
            {"type": "status_update", "projects": [_status("alpha"), _status("beta")]},
            {
                "type": "project_updated",
                "project": "alpha",
                "status": _status("alpha", current_step=4, progress_percent=25.0),
            },
        ]
        sent = asyncio.Event()

        async def handler(connection) -> None:
            for frame in frames:
                await connection.send(json.dumps(frame))
            await sent.wait()

        async def process_request(_connection, request):
            paths.append(request.path)
            return None

        async with serve(
            handler, "127.0.0.1", 0, process_request=process_request
        ) as server:
            port = server.sockets[0].getsockname()[1]
            client = _client(f"http://127.0.0.1:{port}")

            snapshots: list[list] = []
            updates: list = []
            states: list[str] = []

            feed = RealtimeFeed(
                client,
                on_projects=snapshots.append,
                on_project_updated=updates.append,
                on_state=lambda kind, _detail: states.append(kind),
            )
            task = asyncio.create_task(feed.run())
            try:
                for _ in range(200):
                    if snapshots and updates:
                        break
                    await asyncio.sleep(0.01)
                assert snapshots, "no snapshot arrived over the real socket"
                assert updates, "no project_updated arrived over the real socket"
            finally:
                sent.set()
                await feed.stop()
                await asyncio.wait_for(task, timeout=5)
                await client.aclose()

        # The handshake carried the freshly minted single-use ticket.
        assert paths == [f"/ws?ticket={TICKET}"]

        rows = {row.base_name: row for row in snapshots[0]}
        assert set(rows) == {"alpha", "beta"}
        assert rows["alpha"].display_status == "运行中"
        assert rows["alpha"].step_label == "3/16"
        assert rows["alpha"].status_label == "▶ 运行中"
        assert rows["alpha"].progress_label == "19%"

        assert updates[0].current_step == 4
        assert states[0] == CONNECTING
        assert LIVE in states

    asyncio.run(scenario())


def test_reconnect_mints_a_fresh_ticket_each_time_and_backs_off() -> None:
    """Each attempt mints its own ticket (they are single-use) and doubles."""

    async def scenario() -> None:
        tickets: list[str] = []
        client = _client("http://127.0.0.1:8000", tickets)
        connector = _ScriptedConnector(["fail", "fail", "fail", "fail"])
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) >= 4:
                raise _Stop

        feed = RealtimeFeed(
            client,
            on_projects=lambda _rows: None,
            connector=connector,
            sleep=sleep,
        )
        with pytest.raises(_Stop):
            await feed.run()
        await client.aclose()

        assert delays == [1.0, 2.0, 4.0, 8.0], "backoff must double up to the cap"
        # A single-use ticket cannot be reused, so attempts must outnumber mints
        # only by the attempt that stopped the loop before connecting.
        assert len(tickets) == len(connector.urls) == 4

    asyncio.run(scenario())


def test_live_session_resets_the_backoff_budget() -> None:
    """A connection that reached live must not inherit an inflated delay."""

    async def scenario() -> None:
        client = _client("http://127.0.0.1:8000")
        connector = _ScriptedConnector(["fail", "fail", "live", "fail", "fail"])
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) >= 5:
                raise _Stop

        feed = RealtimeFeed(
            client,
            on_projects=lambda _rows: None,
            connector=connector,
            sleep=sleep,
        )
        with pytest.raises(_Stop):
            await feed.run()
        await client.aclose()

        # 1,2 climb, then the live session resets it back to 1 and it climbs again.
        assert delays == [1.0, 2.0, 1.0, 2.0, 4.0]

    asyncio.run(scenario())


def test_ticket_minting_failure_also_backs_off() -> None:
    """A dead backend must be retried, not crashed on."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend down")

    async def scenario() -> None:
        client = _logged_in(
            ControlPlaneClient(
                "http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
            )
        )
        delays: list[float] = []
        states: list[tuple[str, str]] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) >= 2:
                raise _Stop

        feed = RealtimeFeed(
            client,
            on_projects=lambda _rows: None,
            on_state=lambda kind, detail: states.append((kind, detail)),
            connector=_ScriptedConnector(["live", "live"]),
            sleep=sleep,
        )
        with pytest.raises(_Stop):
            await feed.run()
        await client.aclose()

        assert delays == [1.0, 2.0]
        assert states[0][0] == DISCONNECTED
        assert "无法连接" in states[0][1]

    asyncio.run(scenario())


def test_backoff_is_capped() -> None:
    async def scenario() -> None:
        client = _client("http://127.0.0.1:8000")
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) >= 8:
                raise _Stop

        feed = RealtimeFeed(
            client,
            on_projects=lambda _rows: None,
            connector=_ScriptedConnector(["fail"] * 8),
            sleep=sleep,
        )
        with pytest.raises(_Stop):
            await feed.run()
        await client.aclose()

        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]

    asyncio.run(scenario())


def test_malformed_frames_are_ignored_without_killing_the_feed() -> None:
    delivered: list[list] = []

    async def scenario() -> None:
        feed = RealtimeFeed(
            _client("http://127.0.0.1:8000"),
            on_projects=delivered.append,
            connector=_ScriptedConnector(["live"]),
            sleep=lambda _d: asyncio.sleep(0),
        )
        for raw in (
            "not json at all",
            json.dumps([1, 2, 3]),
            json.dumps({"type": "unknown_kind"}),
            json.dumps({"type": "status_update", "projects": "not-a-list"}),
            json.dumps({"type": "project_updated", "status": {"no_base_name": True}}),
            json.dumps({"type": "status_update", "projects": [{"no_base_name": 1}]}),
        ):
            feed._dispatch(raw)
        # A bytes frame and a well-formed frame still work afterwards.
        feed._dispatch(json.dumps({"type": "status_update", "projects": [_status("alpha")]}).encode())
        await feed.client.aclose()

    asyncio.run(scenario())
    assert len(delivered) == 1
    assert delivered[0][0].base_name == "alpha"


def test_an_unkeyable_snapshot_must_not_wipe_the_table() -> None:
    """A malformed non-empty payload is a contract problem, not "no projects".

    Clearing the table here would destroy the operator's view because the
    backend sent something the client could not read.
    """

    delivered: list[list] = []

    async def scenario() -> None:
        feed = RealtimeFeed(
            _client("http://127.0.0.1:8000"), on_projects=delivered.append
        )
        feed._dispatch(json.dumps({"type": "status_update", "projects": [{"x": 1}]}))
        await feed.client.aclose()

    asyncio.run(scenario())
    assert delivered == [], "an unkeyable snapshot must not clear the table"


def test_a_genuine_empty_snapshot_does_clear_the_table() -> None:
    delivered: list[list] = []

    async def scenario() -> None:
        feed = RealtimeFeed(
            _client("http://127.0.0.1:8000"), on_projects=delivered.append
        )
        feed._dispatch(json.dumps({"type": "status_update", "projects": []}))
        await feed.client.aclose()

    asyncio.run(scenario())
    assert delivered == [[]], "no visible projects must empty the table"


def test_stop_is_idempotent_and_safe_before_any_connection() -> None:
    async def scenario() -> None:
        feed = RealtimeFeed(_client("http://127.0.0.1:8000"), on_projects=lambda _r: None)
        await feed.stop()
        await feed.stop()
        await feed.client.aclose()

    asyncio.run(scenario())


def test_normalize_project_requires_a_key_and_defaults_the_rest() -> None:
    with pytest.raises(ValueError):
        normalize_project({"display_status": "运行中"})
    with pytest.raises(ValueError):
        normalize_project("not-a-dict")

    row = normalize_project({"base_name": "alpha"})
    assert row.total_steps == 16
    assert row.progress_label == "0%"
    assert row.status_label == "未知"
    assert row.pending_label == ""


def test_pending_label_prefers_the_human_gate_over_the_error() -> None:
    gate = normalize_project(
        {"base_name": "alpha", "selection_pending": True, "workflow_error": "boom"}
    )
    assert gate.pending_label == "Step 3 选择"

    consult = normalize_project({"base_name": "beta", "consultation_pending": True})
    assert consult.pending_label == "人工咨询"

    error_only = normalize_project({"base_name": "gamma", "workflow_error": "boom"})
    assert error_only.pending_label == "boom"


def test_archived_projects_are_marked_in_the_row_name() -> None:
    row = normalize_project({"base_name": "alpha", "archived": True})
    assert row.archived is True
