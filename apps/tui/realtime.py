"""Realtime project feed over the control plane's ``/ws`` endpoint.

The backend pushes a full ``status_update`` every 2s, plus a ``project_updated``
diff whenever one project changes.  Three backend properties shape this module:

**Tickets are single-use and expire in 60s.**  A ticket authorises the
handshake only -- the server re-reads the account every cycle afterwards.  So
*every* connect attempt mints a fresh ticket, including the first.

**Logs are not pushed.**  Only project status travels over this socket; log
tails are polled separately (M3).  Nothing here should imply otherwise.

**Disconnects are normal, not exceptional.**  A backend restart, an account
being disabled, or an idle-timeout all close the socket.  The feed therefore
reports its state to the caller instead of raising, and keeps reconnecting with
exponential backoff until stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from .client import ControlPlaneClient
from .contracts import ProjectRow, normalize_project

DEFAULT_BACKOFF_INITIAL = 1.0
DEFAULT_BACKOFF_MAX = 30.0

# States reported through ``on_state``.
CONNECTING = "connecting"
LIVE = "live"
DISCONNECTED = "disconnected"

ProjectsCallback = Callable[[list[ProjectRow]], None]
ProjectCallback = Callable[[ProjectRow], None]
StateCallback = Callable[[str, str], None]


class RealtimeFeed:
    """Keeps one authenticated ``/ws`` connection open, reconnecting as needed.

    ``connector`` and ``sleep`` are injection points: tests drive a real local
    WebSocket server, and substitute the sleep so backoff is asserted without
    waiting for wall-clock time.
    """

    def __init__(
        self,
        client: ControlPlaneClient,
        *,
        on_projects: ProjectsCallback,
        on_project_updated: ProjectCallback | None = None,
        on_state: StateCallback | None = None,
        connector: Callable[[str], Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
    ) -> None:
        self.client = client
        self._on_projects = on_projects
        self._on_project_updated = on_project_updated
        self._on_state = on_state
        self._sleep = sleep
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        # Imported lazily so the module stays importable without the optional
        # websocket dependency until a feed is actually constructed.
        if connector is None:
            from websockets.asyncio.client import connect

            connector = connect
        self._connector = connector
        self._stopped = False
        self._connection: Any = None

    # -- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        """Connect, dispatch messages, and reconnect until :meth:`stop`."""

        delay = self._backoff_initial
        while not self._stopped:
            live = False
            failed = False
            try:
                ticket = await self.client.ws_ticket()
                self._state(CONNECTING, "")
                async with self._connector(self.client.websocket_url(ticket)) as connection:
                    self._connection = connection
                    live = True
                    self._state(LIVE, "")
                    async for raw in connection:
                        self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Includes ControlPlaneError (ticket minting), socket errors and
                # protocol failures: all of them mean "retry with backoff".
                failed = True
                self._state(DISCONNECTED, _short(exc))
            finally:
                self._connection = None

            if self._stopped:
                break
            if live:
                # A session that reached the live state resets the budget, so a
                # long-lived connection that drops does not inherit a stale one.
                delay = self._backoff_initial
            if live and not failed:
                self._state(DISCONNECTED, "连接已关闭，正在重连")
            await self._sleep(delay)
            delay = min(delay * 2, self._backoff_max)

    async def stop(self) -> None:
        """Stop reconnecting and close the live connection, if any."""

        self._stopped = True
        connection = self._connection
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()

    # -- internals -------------------------------------------------------

    def _state(self, kind: str, detail: str) -> None:
        if self._on_state is not None:
            self._on_state(kind, detail)

    def _dispatch(self, raw: Any) -> None:
        """Route one frame; malformed or unknown frames are ignored."""

        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            return
        try:
            message = json.loads(raw)
        except ValueError:
            return
        if not isinstance(message, dict):
            return

        kind = message.get("type")
        if kind == "status_update":
            projects = message.get("projects")
            if isinstance(projects, list):
                rows = _rows(projects)
                # An empty raw list is a real "nothing visible" snapshot and
                # must clear the table.  An empty result from a *non-empty*
                # list means every entry was unkeyable -- a contract problem,
                # and one that must not be allowed to wipe the operator's view.
                if rows or not projects:
                    self._on_projects(rows)
        elif kind == "project_updated":
            status = message.get("status")
            if isinstance(status, dict) and self._on_project_updated is not None:
                row = _row(status)
                if row is not None:
                    self._on_project_updated(row)


def _row(payload: Any) -> ProjectRow | None:
    try:
        return normalize_project(payload)
    except ValueError:
        # A project with no base_name cannot be keyed into the table; dropping
        # it is better than rendering every project under one broken row.
        return None


def _rows(payloads: list) -> list[ProjectRow]:
    rows = [_row(item) for item in payloads]
    return [row for row in rows if row is not None]


def _short(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
