"""Response normalisation for the control-plane payloads the TUI renders.

The browser client keeps the same discipline in ``web/frontend/src/lib/contracts.js``:
a field that is missing or the wrong shape degrades to a documented default
instead of raising in the middle of a render.  The backend is the source of
truth for values; nothing here invents workflow meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Session:
    """An authenticated control-plane identity.

    ``access_token`` is the bearer credential; it is never written to disk by
    this package and never rendered.
    """

    access_token: str
    username: str
    role: str = "user"
    status: str = "active"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def as_text(value: Any, default: str = "") -> str:
    """Coerce ``value`` to text, treating ``None`` as absent."""

    if value is None:
        return default
    return str(value)


def normalize_session(payload: Any) -> Session:
    """Build a :class:`Session` from a login response.

    ``access_token`` is required: without it the session cannot authenticate
    anything, so a response missing it is a hard contract failure rather than a
    degraded one.
    """

    if not isinstance(payload, dict):
        raise ValueError("登录响应不是 JSON 对象")
    token = as_text(payload.get("access_token")).strip()
    if not token:
        raise ValueError("登录响应缺少 access_token")
    return Session(
        access_token=token,
        username=as_text(payload.get("username")),
        role=as_text(payload.get("role"), "user"),
        status=as_text(payload.get("status"), "active"),
    )
