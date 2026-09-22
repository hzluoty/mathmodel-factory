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


@dataclass(frozen=True)
class ProjectRow:
    """One project as the terminal renders it.

    Only fields the table actually shows are modelled; the backend's full
    ``ProjectStatus`` carries far more, and copying all of it here would create
    a second contract to keep in sync for no rendering benefit.
    """

    base_name: str
    display_status: str = ""
    current_step: int = 0
    total_steps: int = 16
    progress_percent: float = 0.0
    is_running: bool = False
    archived: bool = False
    consultation_pending: bool = False
    selection_pending: bool = False
    workflow_error: str = ""

    @property
    def step_label(self) -> str:
        return f"{self.current_step}/{self.total_steps}"

    @property
    def progress_label(self) -> str:
        return f"{self.progress_percent:.0f}%"

    @property
    def status_label(self) -> str:
        """Status text with a running marker, so the marker cannot be lost."""

        prefix = "▶ " if self.is_running else ""
        return f"{prefix}{self.display_status or '未知'}"

    @property
    def pending_label(self) -> str:
        """What this project is waiting on, if anything.

        A standing human gate outranks a recorded workflow error: the gate is
        the actionable item, while the error is usually its cause.
        """

        gates: list[str] = []
        if self.selection_pending:
            gates.append("Step 3 选择")
        if self.consultation_pending:
            gates.append("人工咨询")
        if gates:
            return " / ".join(gates)
        if self.workflow_error:
            return self.workflow_error
        return ""


def as_text(value: Any, default: str = "") -> str:
    """Coerce ``value`` to text, treating ``None`` as absent."""

    if value is None:
        return default
    return str(value)


def as_int(value: Any, default: int = 0) -> int:
    """Coerce ``value`` to an int, falling back on anything unusable."""

    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to a float, falling back on anything unusable."""

    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool = False) -> bool:
    """Coerce ``value`` to a bool without treating every truthy string as true."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


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


def normalize_project(payload: Any) -> ProjectRow:
    """Build a :class:`ProjectRow` from one ``ProjectStatus`` payload.

    ``base_name`` identifies the row and is the only required field; a payload
    without it cannot be placed in the table at all.
    """

    if not isinstance(payload, dict):
        raise ValueError("项目状态不是 JSON 对象")
    base_name = as_text(payload.get("base_name")).strip()
    if not base_name:
        raise ValueError("项目状态缺少 base_name")
    return ProjectRow(
        base_name=base_name,
        # The backend sends both a machine ``status`` and a human
        # ``display_status``; the latter is what the dashboard shows.
        display_status=as_text(payload.get("display_status"))
        or as_text(payload.get("status")),
        current_step=as_int(payload.get("current_step")),
        total_steps=as_int(payload.get("total_steps"), 16),
        progress_percent=as_float(payload.get("progress_percent")),
        is_running=as_bool(payload.get("is_running")),
        archived=as_bool(payload.get("archived")),
        consultation_pending=as_bool(payload.get("consultation_pending")),
        selection_pending=as_bool(payload.get("selection_pending")),
        workflow_error=as_text(payload.get("workflow_error")),
    )
