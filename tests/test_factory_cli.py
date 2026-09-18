import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT
from factory_core.storage import SQLiteStateStore


RUNNER = Path(REPO_ROOT) / "run_paper.sh"


def make_project(root: Path, name: str = "demo") -> Path:
    project = root / "ongoing" / name
    (project / "problem").mkdir(parents=True)
    (project / "problem" / "problem_brief.md").write_text("# problem\n", encoding="utf-8")
    (project / "checkpoint.md").write_text(
        f"- **Base name**: {name}\n- **Last completed step**: 4\n",
        encoding="utf-8",
    )
    return project


def test_public_infer_step_reads_authoritative_sqlite_for_migrated_project(tmp_path):
    project = make_project(tmp_path)
    SQLiteStateStore(project).initialize(
        project_id="demo", project_type="modeling", last_completed_step=7
    )

    result = subprocess.run(
        [str(RUNNER), "--infer-step", str(project)],
        env={**os.environ, "FACTORY": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "7"


def test_public_runner_rejects_retired_social_science_execution(tmp_path):
    project = tmp_path / "ongoing" / "social"
    project.mkdir(parents=True)
    (project / "project_brief.md").write_text("# legacy\n", encoding="utf-8")
    (project / "checkpoint.md").write_text(
        "- **Last completed step**: 2\n", encoding="utf-8"
    )

    result = subprocess.run(
        [str(RUNNER), str(project)],
        env={**os.environ, "FACTORY": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 64
    assert "LEGACY_DOMAIN_RETIRED" in result.stderr
