"""Post-login landing screen.

M0 stops here on purpose: this screen proves the session and the endpoint are
real, and M1 replaces it with the live project table driven by ``/ws``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label

from ..contracts import Session


class HomeScreen(Screen[None]):
    """Show the authenticated identity and the endpoint it came from."""

    CSS = """
    HomeScreen {
        align: center middle;
    }

    #home-panel {
        width: 70;
        height: auto;
        border: round $success;
        padding: 1 2;
    }

    #home-title {
        text-style: bold;
        padding-bottom: 1;
    }

    .home-line {
        height: auto;
    }

    #home-next {
        color: $text-muted;
        padding-top: 1;
    }
    """

    BINDINGS = [("q", "quit_app", "退出")]

    def __init__(self, session: Session, base_url: str) -> None:
        super().__init__()
        self.session = session
        self.base_url = base_url

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="home-panel"):
            yield Label("登录成功", id="home-title")
            yield Label(
                f"用户：{self.session.username}（{self.session.role}，{self.session.status}）",
                classes="home-line",
            )
            yield Label(f"后端：{self.base_url}", classes="home-line")
            yield Label("项目列表与实时状态将在 M1 接入。", id="home-next")
        yield Footer()

    def action_quit_app(self) -> None:
        self.app.exit()
