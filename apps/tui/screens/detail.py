"""Project detail: stages, the backend's blocker account, and the live log tail.

Three properties are deliberate.

**One timer, two cadences.**  A log tail only feels live at a few seconds, while
``/steps`` and ``/diagnostics`` are expensive enough that polling them at that
rate would be abusive.  A single interval ticks at the log cadence and refreshes
the heavy projections only every ``HEAVY_EVERY`` ticks.

**Polling is conditioned, not constant.**  Logs are fetched only while the log
pane is on screen and following.  ``/logs`` makes the backend read the entire
file on its event loop before slicing the tail, and these files reach several
megabytes here, so an unconditional poll would block the control plane for data
the operator is not even looking at.  A project that has stopped is fetched once
and then left alone, because its logs are no longer changing.

**One loader per stream, never two in flight.**  A tick arriving mid-load is
dropped rather than queued or cancelled: cancelling would mean a slow project
never finishes, queueing would pile onto an already busy backend.

**No blocker and no logs are not failures.**  Each stream reports its own load
failure distinctly, so "nothing to show" cannot be mistaken for "could not ask".
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, ProgressBar, Static

from ..client import ControlPlaneClient, ControlPlaneError
from ..contracts import DiagnosticsView, LogsView, ProjectRow, StepsView

REFRESH_SECONDS = 5.0
HEAVY_SECONDS = 15.0
LOG_LINES = 200


class ProjectDetailScreen(Screen[None]):
    """Stage progress, the current blocker, and the log tail for one project."""

    CSS = """
    #detail-title {
        height: auto;
        padding: 0 1;
        text-style: bold;
    }

    #detail-meta {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #detail-progress {
        padding: 0 1;
    }

    .section-title {
        padding: 1 1 0 1;
        text-style: bold;
    }

    #blocker-panel {
        height: auto;
        padding: 0 1;
        border: round $warning;
        margin: 0 1;
    }

    #blocker-panel.ok {
        border: round $success;
    }

    #steps-pane, #log-pane {
        height: 1fr;
        margin: 0 1;
    }

    #steps-summary, #log-summary {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }

    #log-body {
        height: auto;
    }

    #log-filter {
        display: none;
        margin: 0 1;
    }

    #detail-status {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    """

    BINDINGS = [
        ("escape", "back", "返回"),
        ("b", "back", "返回"),
        ("l", "toggle_logs", "日志"),
        ("p", "toggle_follow", "暂停/跟随"),
        ("f", "toggle_filter", "过滤"),
        ("end", "jump_to_end", "跳到末尾"),
        ("r", "refresh", "刷新"),
        ("q", "quit_app", "退出"),
    ]

    def __init__(
        self,
        row: ProjectRow,
        client: ControlPlaneClient,
        *,
        refresh_seconds: float = REFRESH_SECONDS,
        heavy_seconds: float = HEAVY_SECONDS,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__()
        self.row = row
        self.client = client
        self._refresh_seconds = refresh_seconds
        self._heavy_seconds = heavy_seconds
        self._auto_refresh = auto_refresh
        self._heavy_loading = False
        self._logs_loading = False
        self._ticks = 0
        self._timer: Any = None
        self._logs_visible = False
        self._follow = True
        self._filter_text = ""
        # Mirrors what is on screen so tests need not read widget internals.
        self.load_error = ""
        self.log_error = ""
        self.steps: StepsView | None = None
        self.diagnostics: DiagnosticsView | None = None
        self.logs: LogsView | None = None

    @property
    def base_name(self) -> str:
        return self.row.base_name

    @property
    def heavy_every(self) -> int:
        """Ticks between heavy refreshes, so one timer serves both cadences."""

        if self._refresh_seconds <= 0:
            return 1
        return max(1, round(self._heavy_seconds / self._refresh_seconds))

    @property
    def logs_visible(self) -> bool:
        return self._logs_visible

    @property
    def following(self) -> bool:
        return self._follow

    # -- composition -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.base_name, id="detail-title")
        yield Static("", id="detail-meta")
        yield ProgressBar(total=100.0, show_eta=False, id="detail-progress")
        yield Static("阻塞与诊断", classes="section-title")
        yield Static("正在加载诊断…", id="blocker-panel")
        yield Static("阶段与产物", classes="section-title", id="steps-heading")
        yield Static("", id="steps-summary")
        with VerticalScroll(id="steps-pane"):
            yield DataTable(id="steps")
        yield Static("日志", classes="section-title", id="log-heading")
        yield Static("", id="log-summary")
        yield Input(placeholder="过滤日志（回车应用，Esc 关闭）", id="log-filter")
        with VerticalScroll(id="log-pane"):
            yield Static("", id="log-body")
        yield Static("正在加载…", id="detail-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#steps", DataTable)
        table.cursor_type = "row"
        table.add_column("阶段", key="step", width=10)
        table.add_column("产物数", key="count", width=8)
        table.add_column("产物", key="artifacts", width=70)
        self._render_summary(self.row)
        self._show_logs(False)
        self._load()
        if self._auto_refresh:
            self._timer = self.set_interval(self._refresh_seconds, self._on_tick)

    async def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # -- actions ---------------------------------------------------------

    def action_back(self) -> None:
        # Closing the filter is the least surprising meaning of "back" while it
        # is open, so the operator cannot be thrown out of the screen by it.
        if self._filter_open():
            self.action_toggle_filter()
            return
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._load()

    def action_toggle_logs(self) -> None:
        self._show_logs(not self._logs_visible)
        if self._logs_visible:
            self._load_logs()

    def action_toggle_follow(self) -> None:
        self._follow = not self._follow
        self._render_logs()
        if self._follow:
            self._scroll_logs_to_end()

    def action_jump_to_end(self) -> None:
        self._follow = True
        self._render_logs()
        self._scroll_logs_to_end()

    def action_toggle_filter(self) -> None:
        field = self.query_one("#log-filter", Input)
        if field.display:
            field.display = False
            self._filter_text = ""
            field.value = ""
            self.query_one("#steps", DataTable).focus()
        else:
            if not self._logs_visible:
                self._show_logs(True)
                self._load_logs()
            field.display = True
            field.value = self._filter_text
            field.focus()
        self._render_logs()

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "log-filter":
            self._filter_text = event.value
            self._render_logs()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "log-filter":
            event.stop()
            self.action_toggle_filter()

    def _filter_open(self) -> bool:
        return self.query_one("#log-filter", Input).display

    # -- timers ----------------------------------------------------------

    def _on_tick(self) -> None:
        self._ticks += 1
        if self._logs_visible and self._follow:
            # A stopped project is fetched once (on open) and then left alone:
            # its logs no longer change, and the endpoint is expensive.
            if self.row.is_running or self.logs is None:
                self._load_logs()
        if self._ticks % self.heavy_every == 0:
            self._load_heavy()

    # -- loading ---------------------------------------------------------

    def _load(self) -> None:
        self._load_heavy()
        if self._logs_visible:
            # Never fetch logs for a pane nobody is looking at: the endpoint
            # reads the whole file on the backend's event loop.
            self._load_logs()

    @work(exclusive=False)
    async def _load_heavy(self) -> None:
        if self._heavy_loading:
            return
        self._heavy_loading = True
        self._set_status("正在加载阶段与诊断…")
        try:
            steps, diagnostics = await asyncio.gather(
                self.client.project_steps(self.base_name),
                self.client.project_diagnostics(self.base_name),
            )
        except ControlPlaneError as exc:
            self.load_error = str(exc)
            self._show_load_failure(str(exc))
            return
        finally:
            self._heavy_loading = False

        self.load_error = ""
        self.steps = steps
        self.diagnostics = diagnostics
        self._render_steps(steps)
        self._render_blocker(diagnostics)
        self._set_status("已更新")

    @work(exclusive=False)
    async def _load_logs(self) -> None:
        if self._logs_loading:
            return
        self._logs_loading = True
        try:
            logs = await self.client.project_logs(self.base_name, lines=LOG_LINES)
        except ControlPlaneError as exc:
            self.log_error = str(exc)
            self.query_one("#log-summary", Static).update(f"日志加载失败：{exc}")
            return
        finally:
            self._logs_loading = False

        self.log_error = ""
        self.logs = logs
        self._render_logs()
        if self._follow:
            self._scroll_logs_to_end()

    # -- rendering -------------------------------------------------------

    def _show_logs(self, visible: bool) -> None:
        self._logs_visible = visible
        for widget_id in ("steps-heading", "steps-summary", "steps-pane"):
            self.query_one(f"#{widget_id}").display = not visible
        for widget_id in ("log-heading", "log-summary", "log-pane"):
            self.query_one(f"#{widget_id}").display = visible
        if not visible:
            self.query_one("#log-filter", Input).display = False
            self.query_one("#steps", DataTable).focus()

    def _render_summary(self, row: ProjectRow) -> None:
        bits = [f"状态 {row.status_label}", f"阶段 {row.step_label}"]
        if row.pending_label:
            bits.append(f"待处理 {row.pending_label}")
        if row.archived:
            bits.append("已归档")
        self.query_one("#detail-meta", Static).update("　·　".join(bits))
        self.query_one("#detail-progress", ProgressBar).update(
            total=100.0, progress=max(0.0, min(100.0, row.progress_percent))
        )

    def _render_blocker(self, diag: DiagnosticsView) -> None:
        panel = self.query_one("#blocker-panel", Static)
        lines = [diag.headline]
        detail = "　·　".join(
            bit
            for bit in (
                f"状态 {diag.state}" if diag.state else "",
                f"动作 {diag.current_action}" if diag.current_action else "",
                f"来源 {diag.source}" if diag.source else "",
            )
            if bit
        )
        if detail:
            lines.append(detail)
        if diag.suggested_actions:
            lines.append("建议动作：" + "、".join(diag.suggested_actions))
        for item in diag.evidence:
            lines.append(f"· {item.label}")
        panel.update("\n".join(lines))
        panel.set_classes(["ok"] if not diag.blocked else [])

    def _render_steps(self, steps: StepsView) -> None:
        table = self.query_one("#steps", DataTable)
        table.clear()
        summary = self.query_one("#steps-summary", Static)
        meaningful = steps.meaningful_steps()
        if not meaningful:
            summary.update("该项目暂无阶段产物")
            return
        for step in meaningful:
            names = "、".join(artifact.name or artifact.path for artifact in step.artifacts)
            marker = "← 当前" if step.index == steps.current_step else ""
            label = f"{step.index} {marker}".strip()
            table.add_row(
                label, str(step.artifact_count), names or "—", key=f"step-{step.index}"
            )
        extra = []
        if steps.verdict:
            extra.append(f"裁判结论 {steps.verdict}")
        extra.append(f"未决问题 {steps.open_issues}")
        extra.append("已有论文" if steps.paper_available else "暂无论文")
        # Its own widget: the load status must not be able to erase this.
        summary.update(f"共 {steps.declared_total} 阶段　·　" + "　·　".join(extra))

    def _render_logs(self) -> None:
        summary = self.query_one("#log-summary", Static)
        logs = self.logs
        if logs is None:
            if not self.log_error:
                summary.update("正在加载日志…")
            return
        shown = logs.filtered(self._filter_text)
        self.query_one("#log-body", Static).update("\n".join(shown))
        bits = [
            logs.file or "(未知文件)",
            f"{len(shown)}/{len(logs.lines)} 行",
            "跟随中" if self._follow else "已暂停（p 恢复）",
        ]
        if self._filter_text:
            bits.append(f"过滤 {self._filter_text!r}")
        if self.log_error:
            bits.append(f"上次加载失败：{self.log_error}")
        bits.append("更新于 " + datetime.now(timezone.utc).strftime("%H:%M:%S"))
        summary.update("　·　".join(bits))

    def _scroll_logs_to_end(self) -> None:
        self.query_one("#log-pane", VerticalScroll).scroll_end(animate=False)

    def _show_load_failure(self, message: str) -> None:
        panel = self.query_one("#blocker-panel", Static)
        panel.set_classes([])
        panel.update(f"诊断加载失败：{message}")
        self._set_status("加载失败，按 r 重试")

    def _set_status(self, message: str) -> None:
        self.query_one("#detail-status", Static).update(f"{self.base_name}　{message}")
