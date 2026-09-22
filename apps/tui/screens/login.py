"""Credential entry.

The password lives in the widget for the duration of one request and is never
logged, stored, or echoed back into an error message.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static

from ..client import ControlPlaneClient, ControlPlaneError
from ..contracts import Session


class LoginScreen(Screen[Session | None]):
    """Collect credentials and dismiss with a :class:`Session`, or ``None`` to quit."""

    CSS = """
    LoginScreen {
        align: center middle;
    }

    #login-panel {
        width: 66;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }

    #login-title {
        text-style: bold;
        padding-bottom: 1;
    }

    #login-endpoint {
        color: $text-muted;
        padding-bottom: 1;
    }

    .field-label {
        padding-top: 1;
    }

    #login-error {
        color: $error;
        padding-top: 1;
        height: auto;
    }

    #login-actions {
        height: auto;
        align: right middle;
        padding-top: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "退出")]

    def __init__(self, client: ControlPlaneClient) -> None:
        super().__init__()
        self.client = client
        # Mirrors the rendered error so tests need not reach into widget internals.
        self.last_error = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="login-panel"):
            yield Label("建模工厂 TUI", id="login-title")
            yield Label(f"后端：{self.client.base_url}", id="login-endpoint")
            yield Label("用户名", classes="field-label")
            yield Input(placeholder="username", id="username")
            yield Label("密码", classes="field-label")
            yield Input(placeholder="password", password=True, id="password")
            yield Static("", id="login-error")
            with Horizontal(id="login-actions"):
                yield Button("登录", variant="primary", id="login")
                yield Button("退出", id="quit")

    def on_mount(self) -> None:
        self.query_one("#username", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._attempt_login()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.dismiss(None)
            return
        if event.button.id == "login":
            self._attempt_login()

    def _set_error(self, message: str) -> None:
        self.last_error = message
        self.query_one("#login-error", Static).update(message)

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#login", Button).disabled = busy

    @work(exclusive=True)
    async def _attempt_login(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        if not username or not password:
            self._set_error("请输入用户名和密码")
            return

        self._set_error("正在登录…")
        self._set_busy(True)
        try:
            session = await self.client.login(username, password)
        except ControlPlaneError as exc:
            # ``str(exc)`` is safe here: the client's messages never echo the
            # submitted password back, only the failure classification.
            self._set_error(str(exc))
            self._set_busy(False)
            return
        self.dismiss(session)
