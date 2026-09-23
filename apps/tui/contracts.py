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


def as_text_list(value: Any) -> tuple[str, ...]:
    """Coerce ``value`` to a tuple of strings, dropping anything else."""

    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item is not None)


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


# --- stage and diagnostics projections ----------------------------------------------
#
# These mirror the nested shape the backend actually returns.  ``/diagnostics``
# nests the blocker under ``status`` in every one of its branches, and the
# human-readable ``reason_summary`` is used as-is: the terminal deliberately
# keeps no reason-code table of its own, so it cannot drift from the backend.


@dataclass(frozen=True)
class Artifact:
    """One produced file, as ``_meta`` reports it."""

    path: str
    name: str = ""
    size: int = 0
    mtime: str = ""


@dataclass(frozen=True)
class Step:
    index: int
    artifacts: tuple[Artifact, ...] = ()

    @property
    def artifact_count(self) -> int:
        return len(self.artifacts)


@dataclass(frozen=True)
class StepsView:
    current_step: int = 0
    steps: tuple[Step, ...] = ()
    verdict: str = ""
    open_issues: int = 0
    paper_available: bool = False

    @property
    def declared_total(self) -> int:
        return len(self.steps)

    def artifacts_for(self, index: int) -> tuple[Artifact, ...]:
        for step in self.steps:
            if step.index == index:
                return step.artifacts
        return ()

    def meaningful_steps(self, floor: int = 0) -> tuple[Step, ...]:
        """Steps worth a row: those with artifacts, plus the current one.

        The contract declares 17 stages; rendering all of them buries the two
        or three that carry information.
        """

        keep = self.current_step if floor <= 0 else max(floor, self.current_step)
        return tuple(
            step for step in self.steps if step.artifacts or step.index == keep
        )


@dataclass(frozen=True)
class Evidence:
    """One evidence pointer.

    ``summary`` carries whatever extra scalar fields the backend attached, so a
    new evidence kind renders without a matching change here.
    """

    kind: str = ""
    path: str = ""
    summary: str = ""

    @property
    def label(self) -> str:
        parts = [part for part in (self.kind, self.path) if part]
        text = " ".join(parts) if parts else "证据"
        return f"{text}（{self.summary}）" if self.summary else text


@dataclass(frozen=True)
class DiagnosticsView:
    source: str = ""
    state: str = ""
    current_step: int = 0
    current_action: str = ""
    reason_code: str = ""
    reason_summary: str = ""
    suggested_actions: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.reason_code or self.reason_summary)

    @property
    def headline(self) -> str:
        if not self.blocked:
            return "无阻塞记录"
        if self.reason_code and self.reason_summary:
            return f"{self.reason_code} — {self.reason_summary}"
        return self.reason_code or self.reason_summary


def _artifacts(value: Any) -> tuple[Artifact, ...]:
    if not isinstance(value, list):
        return ()
    artifacts: list[Artifact] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        artifacts.append(
            Artifact(
                path=as_text(item.get("path")),
                name=as_text(item.get("name")),
                size=as_int(item.get("size")),
                mtime=as_text(item.get("mtime")),
            )
        )
    return tuple(artifacts)


_EVIDENCE_NAMED = {"kind", "path"}


def _evidence(value: Any) -> tuple[Evidence, ...]:
    if not isinstance(value, list):
        return ()
    rows: list[Evidence] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        extras = " ".join(
            f"{key}={item[key]}"
            for key in sorted(item)
            if key not in _EVIDENCE_NAMED
            and isinstance(item[key], (str, int, float, bool))
        )
        rows.append(
            Evidence(
                kind=as_text(item.get("kind")),
                path=as_text(item.get("path")),
                summary=extras,
            )
        )
    return tuple(rows)


def normalize_steps(payload: Any) -> StepsView:
    """Build a :class:`StepsView`. Unusable entries are skipped, not fatal."""

    if not isinstance(payload, dict):
        raise ValueError("阶段投影不是 JSON 对象")
    raw_steps = payload.get("steps")
    steps: list[Step] = []
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if not isinstance(item, dict):
                continue
            steps.append(
                Step(
                    index=as_int(item.get("index"), -1),
                    artifacts=_artifacts(item.get("artifacts")),
                )
            )
    return StepsView(
        current_step=as_int(payload.get("current_step")),
        steps=tuple(steps),
        verdict=as_text(payload.get("verdict")),
        open_issues=as_int(payload.get("open_issues")),
        paper_available=as_bool(payload.get("paper_available")),
    )


def normalize_diagnostics(payload: Any) -> DiagnosticsView:
    """Build a :class:`DiagnosticsView` from the backend's blocker projection."""

    if not isinstance(payload, dict):
        raise ValueError("诊断投影不是 JSON 对象")
    status = payload.get("status")
    if not isinstance(status, dict):
        # Every backend branch nests the blocker under "status"; a payload
        # without it is a contract change, not an empty diagnosis.
        raise ValueError("诊断投影缺少 status")
    return DiagnosticsView(
        source=as_text(payload.get("source")),
        state=as_text(status.get("state")),
        current_step=as_int(status.get("current_step")),
        current_action=as_text(status.get("current_action")),
        reason_code=as_text(status.get("reason_code")),
        reason_summary=as_text(status.get("reason_summary")),
        suggested_actions=as_text_list(status.get("suggested_actions")),
        evidence=_evidence(status.get("evidence")),
    )


@dataclass(frozen=True)
class LogsView:
    """The tail of the single newest log file the backend exposes.

    ``/logs`` returns one file -- the most recently modified non-empty one --
    rather than a per-stage list, so the terminal shows which file this is
    instead of implying the view spans all of them.
    """

    file: str = ""
    lines: tuple[str, ...] = ()

    def filtered(self, needle: str) -> tuple[str, ...]:
        if not needle:
            return self.lines
        lowered = needle.lower()
        return tuple(line for line in self.lines if lowered in line.lower())


def normalize_logs(payload: Any) -> LogsView:
    """Build a :class:`LogsView`.  ``{"logs": []}`` carries no ``file`` key."""

    if not isinstance(payload, dict):
        raise ValueError("日志投影不是 JSON 对象")
    raw = payload.get("logs")
    lines: tuple[str, ...] = ()
    if isinstance(raw, list):
        lines = tuple(as_text(item) for item in raw)
    return LogsView(file=as_text(payload.get("file")), lines=lines)
