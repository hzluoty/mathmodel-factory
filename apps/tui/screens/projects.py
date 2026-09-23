"""Live project table driven by the realtime feed.

Rows are keyed by ``base_name`` and updated in place.  Rebuilding the table on
every 2s push would work, but it would also reset the cursor and scroll position
under the operator's hands twice a second.
"""

from __future__ import annotations

from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static
from textual.widgets.data_table import RowDoesNotExist

from ..client import ControlPlaneClient
from ..contracts import ProjectRow
from ..realtime import CONNECTING, DISCONNECTED, LIVE, RealtimeFeed
from .detail import ProjectDetailScreen

COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("base_name", "项目", 36),
    ("status", "状态", 18),
    ("step", "阶段", 8),
    ("progress", "进度", 8),
    ("pending", "待处理", 26),
)

_STATE_LABEL = {
    LIVE: "● 实时",
    CONNECTING: "◌ 连接中",
    DISCONNECTED: "○ 已断开",
}


class ProjectsScreen(Screen[None]):
    """The project list, kept current by ``/ws``."""

    CSS = """
    #feed-state {
        height: 1;
        padding: 0 1;
        background: $panel;
    }

    #feed-state.live {
        color: $success;
    }

    #feed-state.disconnected {
        color: $error;
    }

    #projects {
        height: 1fr;
    }
    """

    BINDINGS = [
        # Enter is deliberately absent: DataTable binds it to select_cursor, so a
        # screen-level Enter binding never fires while the table has focus.  The
        # row-selected message below is what actually opens the detail.
        ("o", "open_detail", "详情"),
        ("r", "reconnect", "重连"),
        ("q", "quit_app", "退出"),
    ]

    def __init__(
        self,
        client: ControlPlaneClient,
        *,
        realtime_connector: Any = None,
        realtime_sleep: Any = None,
    ) -> None:
        super().__init__()
        self.client = client
        # Injection points for tests; see RealtimeFeed.
        self._realtime_connector = realtime_connector
        self._realtime_sleep = realtime_sleep
        self.feed: RealtimeFeed | None = None
        # Kept so the detail screen can be opened for the row under the cursor;
        # the table itself only stores rendered strings.
        self._rows: dict[str, ProjectRow] = {}
        # Mirrors the rendered feed state so tests need not read widget internals.
        self.feed_state: tuple[str, str] = (CONNECTING, "")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("◌ 正在连接实时通道…", id="feed-state")
        yield DataTable(id="projects")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#projects", DataTable)
        table.cursor_type = "row"
        for key, label, width in COLUMNS:
            table.add_column(label, key=key, width=width)
        self._start_feed()

    async def on_unmount(self) -> None:
        feed = self.feed
        self.feed = None
        if feed is not None:
            await feed.stop()

    # -- feed ------------------------------------------------------------

    @work(exclusive=True)
    async def _start_feed(self) -> None:
        kwargs: dict[str, Any] = {}
        if self._realtime_connector is not None:
            kwargs["connector"] = self._realtime_connector
        if self._realtime_sleep is not None:
            kwargs["sleep"] = self._realtime_sleep
        feed = RealtimeFeed(
            self.client,
            on_projects=self._on_projects,
            on_project_updated=self._on_project_updated,
            on_state=self._on_state,
            **kwargs,
        )
        self.feed = feed
        await feed.run()

    def action_reconnect(self) -> None:
        """Drop the current feed and open a fresh one.

        ``exclusive=True`` cancels the running worker, whose ``async with``
        closes the old socket on the way out.
        """

        self._start_feed()

    # -- feed callbacks --------------------------------------------------

    def _on_state(self, kind: str, detail: str) -> None:
        self.feed_state = (kind, detail)
        status = self.query_one("#feed-state", Static)
        label = _STATE_LABEL.get(kind, kind)
        status.update(f"{label}　{detail}".rstrip())
        status.set_classes([kind] if kind in {LIVE, DISCONNECTED} else [])

    def _on_projects(self, rows: list[ProjectRow]) -> None:
        """Apply a full snapshot: upsert what arrived, drop what vanished."""

        seen = {row.base_name for row in rows}
        for row in rows:
            self._upsert(row)
        for base_name in sorted(set(self._rows) - seen):
            self._remove(base_name)

    def _on_project_updated(self, row: ProjectRow) -> None:
        self._upsert(row)

    # -- table -----------------------------------------------------------

    @staticmethod
    def _cells(row: ProjectRow) -> dict[str, str]:
        name = row.base_name
        if row.archived:
            name = f"{name}（已归档）"
        return {
            "base_name": name,
            "status": row.status_label,
            "step": row.step_label,
            "progress": row.progress_label,
            "pending": row.pending_label,
        }

    def _upsert(self, row: ProjectRow) -> None:
        table = self.query_one("#projects", DataTable)
        cells = self._cells(row)
        if row.base_name in self._rows:
            for key, value in cells.items():
                table.update_cell(row.base_name, key, value)
            self._rows[row.base_name] = row
            return
        table.add_row(*cells.values(), key=row.base_name)
        self._rows[row.base_name] = row

    def _remove(self, base_name: str) -> None:
        table = self.query_one("#projects", DataTable)
        try:
            table.remove_row(base_name)
        except RowDoesNotExist:
            # The table owns its rows; if it already lost this one, only the
            # local index needs correcting.
            pass
        self._rows.pop(base_name, None)

    # -- actions ---------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter (or a click) on the focused table selects a row -> detail."""

        event.stop()
        self._open_detail(str(event.row_key.value))

    def action_open_detail(self) -> None:
        """Open the detail screen for the row under the cursor (``o``)."""

        table = self.query_one("#projects", DataTable)
        if not self._rows or table.row_count == 0:
            return
        cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        self._open_detail(str(cell_key.row_key.value))

    def _open_detail(self, base_name: str) -> None:
        row = self._rows.get(base_name)
        if row is None:
            return
        self.app.push_screen(ProjectDetailScreen(row, self.client))

    def action_quit_app(self) -> None:
        self.app.exit()
