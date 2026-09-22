"""Contract tests for the read-only stage/diagnostics projections.

The timeout test is behavioural rather than asserted-against-a-constant: a slow
transport plus a tiny interactive cap proves the heavy budget is actually
applied per call, which a constant comparison could not.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from apps.tui.client import (
    ControlPlaneClient,
    ControlPlaneError,
    ConnectionFailed,
    ForbiddenError,
)
from apps.tui.contracts import (
    Artifact,
    DiagnosticsView,
    LogsView,
    StepsView,
    normalize_diagnostics,
    normalize_logs,
    normalize_steps,
)

BASE_URL = "http://127.0.0.1:8000"

STEPS_PAYLOAD = {
    "current_step": 6,
    "steps": [
        {
            "index": 0,
            "artifacts": [
                {
                    "path": "problem/problem_brief.md",
                    "name": "problem_brief.md",
                    "type": "md",
                    "group": "step",
                    "size": 1234,
                    "mtime": "2026-09-22 10:00:00",
                }
            ],
        },
        {"index": 5, "artifacts": []},
        {"index": 6, "artifacts": []},
    ],
    "editorial_gate": {"verdict": None},
    "verdict": "PASS",
    "open_issues": 2,
    "open_issue_items": [1, 2],
    "paper_available": True,
}

DIAGNOSTICS_PAYLOAD = {
    "source": "workflow_events",
    "status": {
        "version": 3,
        "state": "waiting",
        "current_step": 3,
        "current_action": "selection_gate_review",
        "reason_code": "STEP3_SELECTION_PENDING",
        "reason_summary": "等待 Step 3 PRIMARY/AUXILIARY 选择",
        "suggested_actions": ["open_selection_gate", "refresh_status"],
        "evidence": [
            {"kind": "file", "path": "selection.json", "revision": 12},
            {"kind": "database", "path": ".factory/state.db"},
        ],
    },
    "events": [],
    "actions": [],
}


async def _start_slow_server(delay: float, payload: dict):
    """A real socket that answers every request after ``delay`` seconds.

    A custom ``httpx`` transport cannot stand in here: timeouts are enforced by
    the concrete transport, so a fake one would never raise ``ReadTimeout`` and
    the test would silently pass whatever the budget was.
    """

    body = json.dumps(payload).encode()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(65536)
            await asyncio.sleep(delay)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _client(handler) -> ControlPlaneClient:
    return ControlPlaneClient(BASE_URL, transport=httpx.MockTransport(handler))


def _routes(routes: dict[str, tuple[int, dict]]):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        status, payload = routes.get(request.url.path, (404, {"detail": "nope"}))
        return httpx.Response(status, json=payload)

    return handler, seen


# --- steps --------------------------------------------------------------------------


def test_project_steps_parses_the_stage_projection() -> None:
    handler, seen = _routes({"/api/projects/alpha/steps": (200, STEPS_PAYLOAD)})

    async def scenario() -> StepsView:
        client = _client(handler)
        view = await client.project_steps("alpha")
        await client.aclose()
        return view

    view = asyncio.run(scenario())
    assert seen == ["/api/projects/alpha/steps"]
    assert view.current_step == 6
    assert view.verdict == "PASS"
    assert view.open_issues == 2
    assert view.paper_available is True
    assert view.declared_total == 3
    artifact = view.artifacts_for(0)[0]
    assert artifact == Artifact(
        path="problem/problem_brief.md",
        name="problem_brief.md",
        size=1234,
        mtime="2026-09-22 10:00:00",
    )


def test_base_name_is_url_escaped() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.raw_path.decode()))
        return httpx.Response(200, json=STEPS_PAYLOAD)

    async def scenario() -> None:
        client = _client(handler)
        await client.project_steps("a b")
        await client.aclose()

    asyncio.run(scenario())
    # httpx decodes url.path, so the escaped form is asserted via raw_path.
    assert seen == [("/api/projects/a b/steps", "/api/projects/a%20b/steps")]


# --- diagnostics --------------------------------------------------------------------


def test_project_diagnostics_parses_the_nested_blocker() -> None:
    handler, seen = _routes(
        {"/api/projects/alpha/diagnostics": (200, DIAGNOSTICS_PAYLOAD)}
    )

    async def scenario() -> DiagnosticsView:
        client = _client(handler)
        view = await client.project_diagnostics("alpha")
        await client.aclose()
        return view

    view = asyncio.run(scenario())
    assert seen == ["/api/projects/alpha/diagnostics"]
    assert view.blocked is True
    assert view.reason_code == "STEP3_SELECTION_PENDING"
    assert view.reason_summary == "等待 Step 3 PRIMARY/AUXILIARY 选择"
    assert view.suggested_actions == ("open_selection_gate", "refresh_status")
    assert [item.kind for item in view.evidence] == ["file", "database"]
    assert view.evidence[0].summary == "revision=12"
    assert view.evidence[1].summary == ""


def test_diagnostics_without_status_is_a_contract_error() -> None:
    handler, _ = _routes({"/api/projects/alpha/diagnostics": (200, {"source": "x"})})

    async def scenario() -> None:
        client = _client(handler)
        with pytest.raises(ControlPlaneError) as caught:
            await client.project_diagnostics("alpha")
        assert "status" in str(caught.value)
        await client.aclose()

    asyncio.run(scenario())


def test_missing_project_grant_raises_forbidden_on_reads() -> None:
    handler, _ = _routes({"/api/projects/alpha/steps": (403, {"detail": "forbidden"})})

    async def scenario() -> None:
        client = _client(handler)
        with pytest.raises(ForbiddenError):
            await client.project_steps("alpha")
        await client.aclose()

    asyncio.run(scenario())


# --- timeouts -----------------------------------------------------------------------


def test_heavy_reads_ignore_the_interactive_timeout() -> None:
    """A 50ms interactive cap must not kill a projection that takes 250ms."""

    async def scenario() -> StepsView:
        server, port = await _start_slow_server(0.25, STEPS_PAYLOAD)
        try:
            client = ControlPlaneClient(f"http://127.0.0.1:{port}", timeout=0.05)
            # An interactive call is held to the 50ms cap.
            with pytest.raises(ConnectionFailed) as caught:
                await client.login("alice", "secret")
            assert "超时" in str(caught.value)
            # The projection is not.
            view = await client.project_steps("alpha")
            await client.aclose()
        finally:
            server.close()
            await server.wait_closed()
        return view

    view = asyncio.run(scenario())
    assert view.current_step == 6


# --- normaliser robustness ----------------------------------------------------------


def test_normalisers_reject_non_objects() -> None:
    with pytest.raises(ValueError):
        normalize_steps("not-a-dict")
    with pytest.raises(ValueError):
        normalize_diagnostics([1, 2, 3])


def test_junk_entries_are_skipped_not_fatal() -> None:
    view = normalize_steps(
        {
            "current_step": "7",
            "steps": [
                "not-a-step",
                {"index": 3, "artifacts": "not-a-list"},
                {"index": 7, "artifacts": [{"path": "p"}, "junk", {"name": "n.md"}]},
            ],
        }
    )
    assert view.current_step == 7
    assert view.declared_total == 2
    assert view.artifacts_for(3) == ()
    assert [a.name for a in view.artifacts_for(7)] == ["", "n.md"]


def test_meaningful_steps_keeps_artifacts_and_the_current_step() -> None:
    view = normalize_steps(
        {
            "current_step": 4,
            "steps": [
                {"index": 0, "artifacts": [{"path": "a"}]},
                {"index": 4, "artifacts": []},
                {"index": 9, "artifacts": []},
            ],
        }
    )
    assert [step.index for step in view.meaningful_steps()] == [0, 4]
    assert view.meaningful_steps(floor=9) and [
        step.index for step in view.meaningful_steps(floor=9)
    ] == [0, 9]


def test_a_clean_project_reports_no_blocker() -> None:
    view = normalize_diagnostics(
        {"source": "workflow_events", "status": {"state": "running", "current_step": 5}}
    )
    assert view.blocked is False
    assert view.headline == "无阻塞记录"


# --- logs ---------------------------------------------------------------------------


def test_project_logs_requests_a_bounded_tail() -> None:
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params.get("lines")))
        return httpx.Response(
            200, json={"logs": ["a", "b"], "file": "step_6_codex.log"}
        )

    async def scenario() -> LogsView:
        client = _client(handler)
        view = await client.project_logs("alpha", lines=50)
        await client.aclose()
        return view

    view = asyncio.run(scenario())
    assert seen == [("/api/projects/alpha/logs", "50")]
    assert view.file == "step_6_codex.log"
    assert view.lines == ("a", "b")


def test_project_logs_clamps_a_nonsensical_line_count() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("lines"))
        return httpx.Response(200, json={"logs": []})

    async def scenario() -> None:
        client = _client(handler)
        await client.project_logs("alpha", lines=0)
        await client.aclose()

    asyncio.run(scenario())
    # The backend would reject 0; asking for at least one line is the sane clamp.
    assert seen == ["1"]


def test_empty_log_payload_has_no_file_key() -> None:
    handler, _ = _routes({"/api/projects/alpha/logs": (200, {"logs": []})})

    async def scenario() -> LogsView:
        client = _client(handler)
        view = await client.project_logs("alpha")
        await client.aclose()
        return view

    view = asyncio.run(scenario())
    assert view.file == ""
    assert view.lines == ()


def test_forbidden_logs_raise_forbidden() -> None:
    handler, _ = _routes({"/api/projects/alpha/logs": (403, {"detail": "forbidden"})})

    async def scenario() -> None:
        client = _client(handler)
        with pytest.raises(ForbiddenError):
            await client.project_logs("alpha")
        await client.aclose()

    asyncio.run(scenario())


def test_normalize_logs_rejects_non_objects_and_keeps_junk_as_text() -> None:
    with pytest.raises(ValueError):
        normalize_logs(["not", "a", "dict"])
    view = normalize_logs({"logs": ["ok", 42, None]})
    # as_text treats None as absent, so a null entry becomes an empty line
    # rather than the string "None".  The backend only ever sends strings here.
    assert view.lines == ("ok", "42", "")
