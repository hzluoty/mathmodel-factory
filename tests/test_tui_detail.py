"""Headless tests for the project detail screen.

Rendering is driven by a duck-typed stub client, so these tests stay about what
the screen shows.  The HTTP layer and its timeout budget are covered separately
in ``tests/test_tui_reads.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from apps.tui.client import ControlPlaneClient, ControlPlaneError
from apps.tui.contracts import (
    ProjectRow,
    Session,
    normalize_diagnostics,
    normalize_steps,
)
from apps.tui.screens.detail import ProjectDetailScreen
from apps.tui.screens.projects import ProjectsScreen

BASE_URL = "http://127.0.0.1:8000"

ROW = ProjectRow(
    base_name="alpha",
    display_status="运行中",
    current_step=6,
    total_steps=16,
    progress_percent=37.5,
    is_running=True,
)

STEPS = normalize_steps(
    {
        "current_step": 6,
        "steps": [
            {
                "index": 0,
                "artifacts": [
                    {"path": "problem/brief.md", "name": "brief.md", "size": 10},
                    {"path": "problem/plan.json", "name": "plan.json", "size": 20},
                ],
            },
            {"index": 5, "artifacts": []},
            {"index": 6, "artifacts": []},
        ],
        "verdict": "REVISE",
        "open_issues": 2,
        "paper_available": False,
    }
)

DIAG_BLOCKED = normalize_diagnostics(
    {
        "source": "workflow_events",
        "status": {
            "state": "waiting",
            "current_step": 3,
            "current_action": "selection_gate_review",
            "reason_code": "STEP3_SELECTION_PENDING",
            "reason_summary": "等待 Step 3 PRIMARY/AUXILIARY 选择",
            "suggested_actions": ["open_selection_gate", "refresh_status"],
            "evidence": [{"kind": "file", "path": "selection.json", "revision": 12}],
        },
    }
)

DIAG_CLEAN = normalize_diagnostics(
    {"source": "workflow_events", "status": {"state": "running", "current_step": 6}}
)


class _StubClient:
    """Duck-typed stand-in for ControlPlaneClient."""

    def __init__(self, steps=None, diagnostics=None, error=None, gate=None) -> None:
        self._steps = steps
        self._diagnostics = diagnostics
        self._error = error
        self._gate = gate
        self.step_calls = 0
        self.diag_calls = 0

    async def project_steps(self, _base_name: str):
        self.step_calls += 1
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        return self._steps

    async def project_diagnostics(self, _base_name: str):
        self.diag_calls += 1
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        return self._diagnostics


class _Harness(App[None]):
    """Runs one screen on its own, so detail tests need no login flow."""

    def __init__(self, screen) -> None:
        super().__init__()
        self._initial = screen

    def on_mount(self) -> None:
        self.push_screen(self._initial)


class _OneShotFeed:
    """A realtime connector that pushes one snapshot, then idles."""

    def __init__(self, projects: list[dict]) -> None:
        self._frame: str | None = json.dumps(
            {"type": "status_update", "projects": projects}
        )

    def __call__(self, _url: str) -> "_OneShotFeed":
        return self

    async def __aenter__(self) -> "_OneShotFeed":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def __aiter__(self) -> "_OneShotFeed":
        return self

    async def __anext__(self) -> str:
        if self._frame is None:
            raise StopAsyncIteration
        frame, self._frame = self._frame, None
        return frame

    async def close(self) -> None:
        return None


async def _park(_delay: float) -> None:
    await asyncio.Event().wait()


async def _settle(pilot, predicate: Callable[[], bool], attempts: int = 80) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


def _text(widget) -> str:
    """Rendered text of a Static, without touching private attributes."""

    visual = getattr(widget, "visual", None)
    return str(visual) if visual is not None else ""


def test_detail_shows_the_blocker_with_actions_and_evidence() -> None:
    async def scenario() -> tuple[str, str]:
        screen = ProjectDetailScreen(
            ROW, _StubClient(STEPS, DIAG_BLOCKED), auto_refresh=False
        )
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.diagnostics is not None)
            panel = _text(screen.query_one("#blocker-panel", Static))
            summary = _text(screen.query_one("#steps-summary", Static))
            return panel, summary

    panel, summary = asyncio.run(scenario())
    assert "STEP3_SELECTION_PENDING" in panel
    assert "等待 Step 3 PRIMARY/AUXILIARY 选择" in panel
    assert "open_selection_gate" in panel
    assert "selection.json" in panel
    assert "revision=12" in panel
    # The stage summary lives on its own line and is not erased by the load
    # status; that regression is what this assertion pins.
    assert "REVISE" in summary
    assert "未决问题 2" in summary


def test_clean_project_says_so_instead_of_looking_empty() -> None:
    async def scenario() -> str:
        screen = ProjectDetailScreen(
            ROW, _StubClient(STEPS, DIAG_CLEAN), auto_refresh=False
        )
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.diagnostics is not None)
            return _text(screen.query_one("#blocker-panel", Static))

    panel = asyncio.run(scenario())
    assert panel.splitlines()[0] == "无阻塞记录"
    assert "STEP3_SELECTION_PENDING" not in panel


def test_load_failure_is_distinguishable_from_no_blocker() -> None:
    """A failed fetch must not render as "nothing is wrong"."""

    async def scenario() -> tuple[str, str]:
        stub = _StubClient(error=ControlPlaneError("诊断不可用"))
        screen = ProjectDetailScreen(ROW, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: bool(screen.load_error))
            return _text(screen.query_one("#blocker-panel", Static)), screen.load_error

    panel, load_error = asyncio.run(scenario())
    assert "诊断加载失败" in panel
    assert "诊断不可用" in panel
    assert "无阻塞记录" not in panel
    assert load_error == "诊断不可用"


def test_steps_table_marks_the_current_step_and_lists_artifacts() -> None:
    async def scenario() -> tuple[list[list[str]], str]:
        screen = ProjectDetailScreen(
            ROW, _StubClient(STEPS, DIAG_CLEAN), auto_refresh=False
        )
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            table = screen.query_one("#steps", DataTable)
            rows = [
                [str(cell) for cell in table.get_row_at(index)]
                for index in range(table.row_count)
            ]
            return rows, _text(screen.query_one("#steps-summary", Static))

    rows, summary = asyncio.run(scenario())
    # Only steps with artifacts, plus the current one, get a row.
    assert len(rows) == 2
    assert rows[0][0] == "0"
    assert rows[0][1] == "2"
    assert "brief.md" in rows[0][2]
    assert "← 当前" in rows[1][0]
    assert rows[1][1] == "0"
    assert "共 3 阶段" in summary


def test_overlapping_refresh_does_not_double_fetch() -> None:
    async def scenario() -> tuple[int, int, bool]:
        gate = asyncio.Event()
        stub = _StubClient(STEPS, DIAG_BLOCKED, gate=gate)
        screen = ProjectDetailScreen(ROW, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: stub.step_calls >= 1)
            # Both of these land while the first load is still in flight.
            screen._load()
            screen.action_refresh()
            for _ in range(20):
                await pilot.pause()
            counts = (stub.step_calls, stub.diag_calls)
            gate.set()
            settled = await _settle(pilot, lambda: screen.steps is not None)
            return counts[0], counts[1], settled

    step_calls, diag_calls, settled = asyncio.run(scenario())
    assert (step_calls, diag_calls) == (1, 1), "in-flight load must absorb the ticks"
    assert settled is True


def test_escape_returns_to_the_previous_screen() -> None:
    async def scenario() -> bool:
        inner = ProjectDetailScreen(
            ROW, _StubClient(STEPS, DIAG_CLEAN), auto_refresh=False
        )
        outer = ProjectsScreen(
            _StubClient(), realtime_connector=_OneShotFeed([]), realtime_sleep=_park
        )
        app = _PushBoth(outer, inner)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.push_screen(inner)
            await pilot.pause()
            assert isinstance(app.screen, ProjectDetailScreen)
            await pilot.press("escape")
            await pilot.pause()
            return isinstance(app.screen, ProjectsScreen)

    assert asyncio.run(scenario()) is True


class _PushBoth(App[None]):
    def __init__(self, first, second) -> None:
        super().__init__()
        self._first = first
        self._second = second

    def on_mount(self) -> None:
        self.push_screen(self._first)


def test_enter_opens_detail_for_the_cursor_row() -> None:
    project = {
        "base_name": "alpha",
        "display_status": "运行中",
        "current_step": 6,
        "total_steps": 16,
        "progress_percent": 37.5,
        "is_running": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/ws-ticket":
            return httpx.Response(200, json={"ticket": "t"})
        if request.url.path == "/api/projects/alpha/steps":
            return httpx.Response(200, json={"current_step": 6, "steps": []})
        if request.url.path == "/api/projects/alpha/diagnostics":
            return httpx.Response(200, json={"status": {"state": "running"}})
        return httpx.Response(404, json={"detail": "nope"})

    async def scenario() -> tuple[bool, str]:
        client = ControlPlaneClient(BASE_URL, transport=httpx.MockTransport(handler))
        # The feed mints a ticket before connecting, so a session must exist.
        client._session = Session(access_token="t", username="alice")
        screen = ProjectsScreen(
            client,
            realtime_connector=_OneShotFeed([project]),
            realtime_sleep=_park,
        )
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: len(screen._rows) == 1)
            # Enter only reaches the table once it holds focus.
            screen.query_one("#projects", DataTable).focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            top = app.screen
            return type(top).__name__, getattr(top, "base_name", "")

    screen_name, base_name = asyncio.run(scenario())
    assert (screen_name, base_name) == ("ProjectDetailScreen", "alpha")
