import json
import os
import subprocess
import hashlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def write_file(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def fake_cloud_client(tmp_path: Path) -> Path:
    client = tmp_path / "fake_gcp_solver_client.sh"
    write_file(
        client,
        "#!/usr/bin/env bash\n"
        "echo CLOUD_CLIENT \"$@\"\n"
        "exit 0\n",
        executable=True,
    )
    return client


def test_gcp_solver_client_describes_service_in_configured_project(tmp_path):
    bin_dir = tmp_path / "bin"
    script = tmp_path / "solve.py"
    gcloud_log = tmp_path / "gcloud.args"
    curl_log = tmp_path / "curl.args"
    write_file(script, "print('ok')\n")
    write_file(
        bin_dir / "gcloud",
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {gcloud_log}\n"
        "if [[ \"$*\" == *'print-identity-token'* ]]; then echo token; else echo https://solver.example; fi\n",
        executable=True,
    )
    write_file(
        bin_dir / "curl",
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {curl_log}\n"
        "if [[ \"$*\" == *'/solve/'* ]]; then\n"
        "  echo '{\"job_id\":\"job-test\"}'\n"
        "else\n"
        "  echo '{\"status\":\"completed\",\"exit_code\":0,\"stdout_url\":null,\"stderr_url\":null,\"result_files\":[]}'\n"
        "fi\n",
        executable=True,
    )
    write_file(
        bin_dir / "gsutil",
        "#!/usr/bin/env bash\nexit 0\n",
        executable=True,
    )

    result = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "gcp_solver_client.sh"),
            "--type",
            "python",
            "--max-time",
            "60",
            str(script),
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GCP_PROJECT_ID": "configured-project",
            "GCP_REGION": "europe-west4",
            "GCP_SOLVER_SERVICE": "solver-api",
            "CLOUD_SOLVER_AUTH_BACKEND": "gcloud",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--project=configured-project" in gcloud_log.read_text(encoding="utf-8")
    assert "Bearer token" not in curl_log.read_text(encoding="utf-8")
    assert "print('ok')" not in curl_log.read_text(encoding="utf-8")


def test_gcp_solver_client_handles_large_working_files_without_argv_overflow(tmp_path):
    bin_dir = tmp_path / "bin"
    script = tmp_path / "solve.py"
    working_file = tmp_path / "large_payload.txt"
    request_capture = tmp_path / "request.json"
    write_file(script, "print('ok')\n")
    write_file(working_file, "x" * 300_000)
    write_file(
        bin_dir / "gcloud",
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *'print-identity-token'* ]]; then echo token; else echo https://solver.example; fi\n",
        executable=True,
    )
    write_file(
        bin_dir / "curl",
        "#!/usr/bin/env bash\n"
        f"capture={request_capture}\n"
        "capture_next=0\n"
        "for arg in \"$@\"; do\n"
        "  if [[ \"$capture_next\" == 1 ]]; then cp \"${arg#@}\" \"$capture\"; capture_next=0; continue; fi\n"
        "  [[ \"$arg\" == '--data-binary' ]] && capture_next=1\n"
        "done\n"
        "if [[ \"$*\" == *'/solve/'* ]]; then\n"
        "  echo '{\"job_id\":\"job-test\"}'\n"
        "else\n"
        "  echo '{\"status\":\"completed\",\"exit_code\":0,\"stdout_url\":null,\"stderr_url\":null,\"result_files\":[]}'\n"
        "fi\n",
        executable=True,
    )
    write_file(bin_dir / "gsutil", "#!/usr/bin/env bash\nexit 0\n", executable=True)

    result = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "gcp_solver_client.sh"),
            "--type",
            "python",
            "--max-time",
            "60",
            "--working-file",
            str(working_file),
            str(script),
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CLOUD_SOLVER_AUTH_BACKEND": "gcloud",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(request_capture.read_text(encoding="utf-8"))
    assert payload["script_content"] == "print('ok')\n"
    assert payload["working_files"][working_file.name] == "x" * 300_000
    assert payload["requested_input_sha256"] == {
        working_file.name: hashlib.sha256(working_file.read_bytes()).hexdigest()
    }


def test_direct_cloud_client_rejects_runtime_not_in_capability_manifest(tmp_path):
    script = tmp_path / "solve.jl"
    write_file(script, "println(1)\n")

    result = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "gcp_solver_client.sh"),
            "--type",
            "julia",
            str(script),
        ],
        env={**os.environ},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "runtime is not available" in result.stderr
