"""Tests for the log tail pane.

Ticks are driven by calling the timer callback directly instead of waiting on
wall-clock intervals, so the polling cadence and its conditions are asserted
deterministically and the suite stays fast.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from textual.app import App
from textual.widgets import Input, Static

from apps.tui.client import ControlPlaneError
from apps.tui.contracts import LogsView, ProjectRow
from apps.tui.screens.detail import ProjectDetailScreen

RUNNING = ProjectRow(
    base_name="alpha",
    display_status="运行中",
    current_step=6,
    total_steps=16,
    progress_percent=37.5,
    is_running=True,
)

STOPPED = ProjectRow(
    base_name="alpha",
    display_status="已完成",
    current_step=16,
    total_steps=16,
    progress_percent=100.0,
    is_running=False,
)


class _StubClient:
    def __init__(self, logs=None, log_error=None) -> None:
        self.logs = logs if logs is not None else LogsView()
        self.log_error = log_error
        self.log_calls = 0
        self.step_calls = 0
        self.diag_calls = 0

    async def project_steps(self, _base_name: str):
        from apps.tui.contracts import StepsView

        self.step_calls += 1
        return StepsView()

    async def project_diagnostics(self, _base_name: str):
        from apps.tui.contracts import normalize_diagnostics

        self.diag_calls += 1
        return normalize_diagnostics({"status": {"state": "running"}})

    async def project_logs(self, _base_name: str, *, lines: int = 200):
        self.log_calls += 1
        if self.log_error is not None:
            raise self.log_error
        return self.logs


class _Harness(App[None]):
    def __init__(self, screen) -> None:
        super().__init__()
        self._initial = screen

    def on_mount(self) -> None:
        self.push_screen(self._initial)


async def _settle(pilot, predicate: Callable[[], bool], attempts: int = 60) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


async def _tick(pilot, screen, times: int = 1) -> None:
    """Drive the timer callback, letting each tick's workers finish."""

    for _ in range(times):
        screen._on_tick()
        for _ in range(4):
            await pilot.pause()


def _text(screen, widget_id: str) -> str:
    widget = screen.query_one(f"#{widget_id}", Static)
    visual = getattr(widget, "visual", None)
    return str(visual) if visual is not None else ""


def _logs(*lines: str, file: str = "step_6_codex.log") -> LogsView:
    return LogsView(file=file, lines=tuple(lines))


def test_logs_are_not_fetched_while_the_pane_is_hidden() -> None:
    async def scenario() -> tuple[int, bool]:
        stub = _StubClient(_logs("a"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await _tick(pilot, screen, times=3)
            return stub.log_calls, screen.logs_visible

    log_calls, visible = asyncio.run(scenario())
    assert log_calls == 0, "hidden panes must not cost the backend a file read"
    assert visible is False


def test_showing_the_pane_fetches_and_renders_the_tail() -> None:
    async def scenario() -> tuple[int, str, str]:
        stub = _StubClient(_logs("line one", "line two", "line three"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            assert await _settle(pilot, lambda: stub.log_calls == 1)
            await pilot.pause()
            return stub.log_calls, _text(screen, "log-body"), _text(screen, "log-summary")

    log_calls, body, summary = asyncio.run(scenario())
    assert log_calls == 1
    assert body.splitlines() == ["line one", "line two", "line three"]
    assert "step_6_codex.log" in summary
    assert "3/3 行" in summary
    assert "跟随中" in summary


def test_pause_freezes_the_view_and_resuming_catches_up() -> None:
    async def scenario() -> tuple[str, str, str]:
        stub = _StubClient(_logs("first"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            assert await _settle(pilot, lambda: screen.logs is not None)
            before = _text(screen, "log-body")

            await pilot.press("p")
            await pilot.pause()
            paused_summary = _text(screen, "log-summary")
            stub.logs = _logs("first", "second")
            await _tick(pilot, screen, times=3)
            frozen = _text(screen, "log-body")

            await pilot.press("p")
            await _tick(pilot, screen, times=2)
            resumed = _text(screen, "log-body")
            return before, paused_summary + "||" + frozen, resumed

    before, frozen_blob, resumed = asyncio.run(scenario())
    assert before == "first"
    assert "已暂停" in frozen_blob
    assert frozen_blob.endswith("first"), "a paused pane must not move"
    assert resumed.splitlines() == ["first", "second"]


def test_filter_narrows_the_visible_lines() -> None:
    async def scenario() -> tuple[str, str]:
        stub = _StubClient(_logs("solve ok", "WARN retry", "solve done"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            assert await _settle(pilot, lambda: screen.logs is not None)
            await pilot.press("f")
            await pilot.pause()
            screen.query_one("#log-filter", Input).value = "warn"
            await pilot.pause()
            return _text(screen, "log-body"), _text(screen, "log-summary")

    body, summary = asyncio.run(scenario())
    assert body == "WARN retry", "filtering must be case-insensitive"
    assert "1/3 行" in summary
    assert "过滤 'warn'" in summary


def test_escape_closes_the_filter_before_leaving_the_screen() -> None:
    async def scenario() -> tuple[bool, bool]:
        stub = _StubClient(_logs("a"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            await pilot.press("f")
            await pilot.pause()
            opened = screen.query_one("#log-filter", Input).display
            await pilot.press("escape")
            await pilot.pause()
            return opened, screen.query_one("#log-filter", Input).display

    opened, still_open = asyncio.run(scenario())
    assert opened is True
    assert still_open is False, "escape must close the filter, not the screen"


def test_log_load_failure_is_shown_not_silently_empty() -> None:
    async def scenario() -> str:
        stub = _StubClient(log_error=ControlPlaneError("日志接口 500"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            assert await _settle(pilot, lambda: bool(screen.log_error))
            return _text(screen, "log-summary")

    summary = asyncio.run(scenario())
    assert "日志加载失败" in summary
    assert "日志接口 500" in summary


def test_ticks_poll_logs_only_when_visible_following_and_unfinished() -> None:
    async def scenario() -> tuple[int, int, int, int]:
        stub = _StubClient(_logs("a"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await _tick(pilot, screen, times=2)
            hidden = stub.log_calls

            await pilot.press("l")
            assert await _settle(pilot, lambda: stub.log_calls == 1)
            await _tick(pilot, screen, times=2)
            visible = stub.log_calls

            await pilot.press("p")
            await _tick(pilot, screen, times=3)
            paused = stub.log_calls
            await pilot.press("p")
            await pilot.pause()
            return hidden, visible, paused, stub.log_calls

    hidden, visible, paused, _ = asyncio.run(scenario())
    assert hidden == 0
    assert visible == 3, "a visible, following pane polls once per tick"
    assert paused == 3, "a paused pane must stop polling entirely"


def test_a_finished_project_is_fetched_once_then_left_alone() -> None:
    async def scenario() -> tuple[int, int]:
        stub = _StubClient(_logs("final line"))
        screen = ProjectDetailScreen(STOPPED, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            assert await _settle(pilot, lambda: stub.log_calls == 1)
            await _tick(pilot, screen, times=4)
            first = stub.log_calls
            # An explicit refresh still fetches, because the user asked.
            screen.action_refresh()
            await _settle(pilot, lambda: stub.log_calls > first)
            return first, stub.log_calls

    quiet, after_manual = asyncio.run(scenario())
    assert quiet == 1, "a stopped project's logs do not change; do not keep asking"
    assert after_manual == 2


def test_heavy_projections_refresh_on_their_own_slower_cadence() -> None:
    async def scenario() -> tuple[int, int]:
        stub = _StubClient(_logs("a"))
        screen = ProjectDetailScreen(
            RUNNING, stub, refresh_seconds=1.0, heavy_seconds=3.0, auto_refresh=False
        )
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            mounted = stub.step_calls
            await _tick(pilot, screen, times=3)
            after_three = stub.step_calls
            await _tick(pilot, screen, times=3)
            return mounted, after_three, stub.step_calls

    mounted, after_three, after_six = asyncio.run(scenario())
    assert mounted == 1
    assert after_three == 2, "one heavy refresh per three ticks"
    assert after_six == 3


def test_toggling_the_pane_back_restores_the_stage_view() -> None:
    async def scenario() -> tuple[bool, bool]:
        stub = _StubClient(_logs("a"))
        screen = ProjectDetailScreen(RUNNING, stub, auto_refresh=False)
        app = _Harness(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            assert await _settle(pilot, lambda: screen.steps is not None)
            await pilot.press("l")
            await pilot.pause()
            shown = screen.query_one("#log-pane").display
            await pilot.press("l")
            await pilot.pause()
            return shown, screen.query_one("#steps-pane").display

    log_shown, steps_shown = asyncio.run(scenario())
    assert log_shown is True
    assert steps_shown is True
