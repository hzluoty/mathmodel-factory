import shutil
import subprocess

import pytest

from factory_core.adapters.infrastructure.process import ProcessResult
from factory_core.adapters.models.backends import CodexCliBackend, ModelRequest


class RecordingSupervisor:
    def run(self, request):
        self.request = request
        return ProcessResult(0, False, 0.01, 123)


def isolated_command(tmp_path):
    supervisor = RecordingSupervisor()
    result = CodexCliBackend(tmp_path, supervisor).execute(ModelRequest(
        project_dir=tmp_path, step_id=13, attempt=1, prompt="Review the evidence",
        timeout_seconds=10, hang_timeout_seconds=5, model="gpt-6-astra", effort="xhigh",
        isolated=True, workdir=tmp_path / "isolated", final_response_file=tmp_path / "verdict.txt",
    ))
    assert result.returncode == 0
    return list(supervisor.request.argv)


def test_isolated_judge_keeps_scope_model_and_verdict_contract(tmp_path):
    argv = isolated_command(tmp_path)
    assert "--full-auto" not in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert 'approval_policy="never"' in argv
    assert "--ephemeral" in argv
    assert argv[argv.index("--model") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="xhigh"' in argv
    assert argv[argv.index("-C") + 1] == str(tmp_path / "isolated")
    assert argv[argv.index("--output-last-message") + 1] == str(tmp_path / "verdict.txt")


def test_isolated_command_is_accepted_by_installed_cli_parser(tmp_path):
    if not shutil.which("codex"):
        pytest.skip("Codex CLI is not installed")
    argv = isolated_command(tmp_path)
    # Help validates CLI flags without launching a model or spending account usage.
    result = subprocess.run(argv[:-1] + ["--help"], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "unexpected argument" not in result.stderr


def test_regular_worker_invocation_is_unchanged(tmp_path):
    supervisor = RecordingSupervisor()
    CodexCliBackend(tmp_path, supervisor).execute(ModelRequest(
        project_dir=tmp_path, step_id=11, attempt=1, prompt="review",
        timeout_seconds=10, hang_timeout_seconds=5,
    ))
    argv = list(supervisor.request.argv)
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--ephemeral" not in argv
