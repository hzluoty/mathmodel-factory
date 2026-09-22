"""Async client for the Web control plane's REST and WebSocket surface.

Every value the TUI shows comes from an endpoint ``web/backend`` already serves;
this module adds transport, auth-header handling and error classification, and
nothing else.

Two properties are deliberate:

**Failures are typed, not stringly.**  A rejected credential, a missing project
grant and an unreachable backend need three different messages on screen and
three different retry policies, so they surface as three exception classes
rather than one error with a status code attached.

**One timeout does not fit all calls.**  The dashboard caps interactive requests
at 15s, but the heavy projections (``/diagnostics``, ``/steps``) legitimately
exceed that on large projects, so callers may pass a longer per-request timeout.
"""

from __future__ import annotations

from typing import Any

import httpx

from .contracts import Session, normalize_session

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Interactive ceiling, matching the browser client's own 15s cap.
DEFAULT_TIMEOUT = 15.0


class ControlPlaneError(RuntimeError):
    """Base class for every failure this client reports."""


class ConnectionFailed(ControlPlaneError):
    """The backend could not be reached, or the socket timed out."""


class AuthError(ControlPlaneError):
    """Credentials were rejected, or the session is no longer valid."""


class ForbiddenError(ControlPlaneError):
    """The account is valid but holds no grant for the target project."""


class ControlPlaneClient:
    """A single control-plane connection, including its authenticated session.

    ``transport`` exists so tests can drive a fake backend without a socket.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session: Session | None = None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    # -- session ---------------------------------------------------------

    @property
    def session(self) -> Session | None:
        """The current session, or ``None`` before a successful login."""

        return self._session

    @property
    def is_authenticated(self) -> bool:
        return self._session is not None

    def _auth_headers(self) -> dict[str, str]:
        if self._session is None:
            return {}
        return {"Authorization": f"Bearer {self._session.access_token}"}

    # -- transport -------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Perform one request, mapping transport and HTTP failures to types."""

        try:
            response = await self._client.request(
                method,
                path,
                headers=self._auth_headers(),
                timeout=self._timeout if timeout is None else timeout,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ConnectionFailed(f"请求超时（{path}）：后端未在时限内响应") from exc
        except httpx.HTTPError as exc:
            raise ConnectionFailed(f"无法连接 {self.base_url}：{exc}") from exc

        if response.status_code == 401:
            raise AuthError("凭据无效或登录已过期")
        if response.status_code == 403:
            raise ForbiddenError("当前账号没有访问该项目的权限")
        if response.status_code >= 400:
            raise ControlPlaneError(_error_detail(response))
        return response

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise ControlPlaneError(f"{path} 返回的不是合法 JSON") from exc

    # -- auth ------------------------------------------------------------

    async def login(self, username: str, password: str) -> Session:
        """Authenticate and retain the resulting session.

        The password is used once and never stored.
        """

        payload = await self._request_json(
            "POST",
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        try:
            session = normalize_session(payload)
        except ValueError as exc:
            raise ControlPlaneError(str(exc)) from exc
        self._session = session
        return session

    async def logout(self) -> None:
        """Best-effort server-side logout; the local session clears regardless."""

        try:
            if self._session is not None:
                await self._request("POST", "/api/auth/logout")
        except ControlPlaneError:
            # A backend that is already gone must not trap the user in a session.
            pass
        finally:
            self._session = None

    async def ws_ticket(self) -> str:
        """Mint a single-use realtime ticket (60s TTL, per the backend)."""

        if self._session is None:
            raise AuthError("尚未登录，无法申请实时票据")
        payload = await self._request_json("POST", "/api/auth/ws-ticket")
        ticket = payload.get("ticket") if isinstance(payload, dict) else None
        if not ticket:
            raise ControlPlaneError("后端未返回 WebSocket 票据")
        return str(ticket)

    def websocket_url(self, ticket: str) -> str:
        """Build the ``/ws`` URL for ``ticket``, matching the backend scheme."""

        if "://" not in self.base_url:
            raise ControlPlaneError(f"非法 base_url：{self.base_url}")
        scheme, _, host = self.base_url.partition("://")
        ws_scheme = "wss" if scheme == "https" else "ws"
        return f"{ws_scheme}://{host}/ws?ticket={ticket}"

    # -- lifecycle -------------------------------------------------------

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ControlPlaneClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


def _error_detail(response: httpx.Response) -> str:
    """Prefer the backend's own ``detail`` over a bare status code."""

    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if detail:
        return str(detail)
    return f"HTTP {response.status_code}"
