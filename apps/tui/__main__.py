"""Entry point for ``factory-tui`` / ``python -m apps.tui``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys

from .app import PaperFactoryTui
from .client import DEFAULT_BASE_URL


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory-tui",
        description="Modeling Factory Web 控制台的终端客户端（只读监控）。",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("FACTORY_TUI_BASE_URL") or DEFAULT_BASE_URL,
        help=f"控制平面地址（默认 {DEFAULT_BASE_URL}，或用 FACTORY_TUI_BASE_URL）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    app = PaperFactoryTui(args.base_url)
    try:
        app.run()
    finally:
        # ``app.run`` returns once the terminal is restored.  The client's pool
        # was created on the app's (now closed) loop, so releasing it here can
        # legitimately fail on some transports; the process is exiting either
        # way, and a shutdown path must not raise.
        with contextlib.suppress(Exception):
            asyncio.run(app.client.aclose())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
