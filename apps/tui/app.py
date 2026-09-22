"""The TUI application shell.

The app owns the control-plane client and the authenticated session; screens
stay render-only so a screen can be driven in tests without a live backend.
"""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from .client import DEFAULT_BASE_URL, ControlPlaneClient
from .contracts import Session
from .screens.home import HomeScreen
from .screens.login import LoginScreen


class PaperFactoryTui(App[None]):
    """Terminal client for the Modeling Factory Web control plane."""

    TITLE = "建模工厂 TUI"

    CSS = """
    Screen {
        align: center middle;
    }
    """

    BINDINGS = [Binding("ctrl+q", "quit", "退出")]

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        client: ControlPlaneClient | None = None,
    ) -> None:
        super().__init__()
        self.base_url = base_url
        # Injection point for tests: a client backed by a fake transport.
        self.client = client if client is not None else ControlPlaneClient(base_url)
        self.session: Session | None = None

    def on_mount(self) -> None:
        self.push_screen(LoginScreen(self.client), self._after_login)

    def _after_login(self, session: Session | None) -> None:
        if session is None:
            # The user quit at the login screen; do not fall through to a shell
            # with no identity.
            self.exit()
            return
        self.session = session
        self.sub_title = f"{session.username}（{session.role}） · {self.base_url}"
        self.push_screen(HomeScreen(session, self.base_url))
