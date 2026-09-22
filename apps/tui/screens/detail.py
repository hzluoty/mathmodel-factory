"""Project detail: stage/artifact state plus the backend's blocker account.

Two properties are deliberate.

**One loader, never two in flight.**  ``/steps`` and ``/diagnostics`` run in the
backend's threadpool and can take seconds on a large project, so the refresh is
guarded: an interval tick that arrives while a load is still running is dropped
rather than queued or cancelled.  Cancelling would mean a slow project never
finishes a load; queueing would pile requests onto an already busy backend.  The
backend has been saturated by aggressive polling before, so the interval is a
deliberate 15s rather than the 8s a purely local UI could afford.

**Absence of a blocker is not the same as a failed load.**  A project with no
recorded blocker and a project whose diagnostics could not be fetched must not
look alike, so load failures take over the panel instead of leaving it empty.
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, ProgressBar, Static

from ..client import ControlPlaneClient, ControlPlaneError
from ..contracts import DiagnosticsView, ProjectRow, StepsView

REFRESH_SECONDS = 15.0


class ProjectDetailScreen(Screen[None]):
    """Stage progress, artifacts and the current blocker for one project."""

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

    #steps {
        height: 1fr;
        margin: 0 1;
    }

    #detail-status {
        height: 1;
        padding: 0 1;
        background: $panel;
    }

    #steps-summary {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("escape", "back", "返回"),
        ("b", "back", "返回"),
        ("r", "refresh", "刷新"),
        ("q", "quit_app", "退出"),
    ]

    def __init__(
        self,
        row: ProjectRow,
        client: ControlPlaneClient,
        *,
        refresh_seconds: float = REFRESH_SECONDS,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__()
        self.row = row
        self.client = client
        self._refresh_seconds = refresh_seconds
        self._auto_refresh = auto_refresh
        self._loading = False
        self._timer: Any = None
        # Mirrors what is on screen so tests need not read widget internals.
        self.load_error = ""
        self.steps: StepsView | None = None
        self.diagnostics: DiagnosticsView | None = None

    @property
    def base_name(self) -> str:
        return self.row.base_name

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.base_name, id="detail-title")
        yield Static("", id="detail-meta")
        yield ProgressBar(total=100.0, show_eta=False, id="detail-progress")
        yield Static("阻塞与诊断", classes="section-title")
        yield Static("正在加载诊断…", id="blocker-panel")
        yield Static("阶段与产物", classes="section-title")
        yield Static("", id="steps-summary")
        yield VerticalScroll(DataTable(id="steps"))
        yield Static("正在加载…", id="detail-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#steps", DataTable)
        table.cursor_type = "row"
        table.add_column("阶段", key="step", width=10)
        table.add_column("产物数", key="count", width=8)
        table.add_column("产物", key="artifacts", width=70)
        self._render_summary(self.row)
        self._load()
        if self._auto_refresh:
            self._timer = self.set_interval(self._refresh_seconds, self._on_tick)

    async def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # -- loading ---------------------------------------------------------

    def _on_tick(self) -> None:
        self._load()

    def action_refresh(self) -> None:
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit_app(self) -> None:
        self.app.exit()

    @work(exclusive=False)
    async def _load(self) -> None:
        if self._loading:
            # A tick that lands mid-load is dropped: the in-flight load already
            # carries the newest answer this screen can use.
            return
        self._loading = True
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
            self._loading = False

        self.load_error = ""
        self.steps = steps
        self.diagnostics = diagnostics
        self._render_steps(steps)
        self._render_blocker(diagnostics)
        self._set_status("已更新")

    # -- rendering -------------------------------------------------------

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
        detail_bits = [
            f"状态 {diag.state}" if diag.state else "",
            f"动作 {diag.current_action}" if diag.current_action else "",
            f"来源 {diag.source}" if diag.source else "",
        ]
        detail = "　·　".join(bit for bit in detail_bits if bit)
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
            table.add_row(label, str(step.artifact_count), names or "—", key=f"step-{step.index}")
        total = steps.declared_total
        extra = []
        if steps.verdict:
            extra.append(f"裁判结论 {steps.verdict}")
        extra.append(f"未决问题 {steps.open_issues}")
        extra.append("已有论文" if steps.paper_available else "暂无论文")
        # Written to its own widget: the load status must not be able to erase
        # the stage summary, which is what happened when both shared one line.
        summary.update(f"共 {total} 阶段　·　" + "　·　".join(extra))

    def _show_load_failure(self, message: str) -> None:
        panel = self.query_one("#blocker-panel", Static)
        panel.set_classes([])
        panel.update(f"诊断加载失败：{message}")
        self._set_status("加载失败，按 r 重试")

    def _set_status(self, message: str) -> None:
        self.query_one("#detail-status", Static).update(f"{self.base_name}　{message}")
