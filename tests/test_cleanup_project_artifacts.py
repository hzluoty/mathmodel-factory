"""Regression tests for the fail-closed project cleanup policy.

The previous revision removed ``data/final`` (and the other ``final`` trees)
wholesale by directory name.  Those trees feed the final input manifest, so
removing them could delete deliverable data before the manifest that was meant
to prove it existed.  These tests pin the fail-closed contract instead:

* only explicitly declared rebuildable/temporary paths are ever candidates,
* anything authoritative (contract artifact, machine evidence, receipt
  reference) is protected,
* an unrecognised path is kept,
* a directory is never removed whole while it still carries a protected member.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.cleanup_project_artifacts import (
    build_plan,
    execute_plan,
    main,
    policy_fingerprint,
    receipt_referenced_paths,
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "ongoing" / "demo"
    project.mkdir(parents=True)
    return project


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _planned(project: Path) -> set[str]:
    plan = build_plan(project)
    names = {str(path.relative_to(project)) for path in plan.delete_files}
    names |= {str(path.relative_to(project)) for path in plan.delete_dirs}
    return names


def test_data_final_is_never_a_deletion_candidate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    final = _write(project / "data" / "final" / "canonical_results.json", "{}")
    scratch = _write(project / "data" / "final" / "adopted_dataset.csv")

    planned = _planned(project)

    assert "data/final" not in planned
    assert not any(name.startswith("data/final") for name in planned)
    assert final.is_file() and scratch.is_file()


def test_default_run_reports_without_deleting(tmp_path: Path) -> None:
    project = _project(tmp_path)
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    plan = build_plan(project)
    references = receipt_referenced_paths(project)
    count, _bytes, had_errors = execute_plan(plan, project, references, execute=False)

    assert count >= 1
    assert had_errors is False
    assert scratch.is_file(), "report mode must not delete anything"


def test_execute_removes_rebuildable_intermediate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    references = receipt_referenced_paths(project)
    plan = build_plan(project, references=references)
    _count, _bytes, had_errors = execute_plan(plan, project, references, execute=True)

    assert had_errors is False
    assert not scratch.exists()


def test_machine_evidence_inside_intermediate_is_protected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    receipt = _write(
        project / "data" / "intermediate" / "solver_receipt_abc.json", "{}"
    )

    plan = build_plan(project)
    kept = {str(path.relative_to(project)) for path in plan.keep_files}
    assert "data/intermediate" in kept

    references = receipt_referenced_paths(project)
    _count, _bytes, _errors = execute_plan(plan, project, references, execute=True)
    assert receipt.is_file(), "machine evidence must survive cleanup"


def test_contract_artifact_blocks_wholesale_directory_removal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    canonical = _write(project / "data" / "intermediate" / "canonical_results.json", "{}")
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    references = receipt_referenced_paths(project)
    plan = build_plan(project, references=references)
    assert "data/intermediate" not in {
        str(path.relative_to(project)) for path in plan.delete_dirs
    }

    _count, _bytes, had_errors = execute_plan(plan, project, references, execute=True)
    assert had_errors is False
    assert canonical.is_file(), "canonical result must never be collected as scratch"
    assert not scratch.exists(), "unprotected sibling is still collected"


def test_receipt_referenced_path_is_protected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    referenced = _write(project / "data" / "intermediate" / "keep_me.csv")
    _write(
        project / ".factory" / "receipts" / "solver.json",
        json.dumps({"outputs": ["data/intermediate/keep_me.csv"]}),
    )

    references = receipt_referenced_paths(project)
    assert "data/intermediate/keep_me.csv" in references

    plan = build_plan(project, references=references)
    _count, _bytes, _errors = execute_plan(plan, project, references, execute=True)
    assert referenced.is_file(), "receipt-referenced input must survive cleanup"


def test_unknown_top_level_directory_is_kept(tmp_path: Path) -> None:
    project = _project(tmp_path)
    uploaded = _write(project / "user_uploads" / "raw_data.csv")

    references = receipt_referenced_paths(project)
    plan = build_plan(project, references=references)
    _count, _bytes, _errors = execute_plan(plan, project, references, execute=True)

    assert uploaded.is_file(), "unrecognised paths must default to keep"


def test_cli_writes_report_and_defaults_to_report_mode(tmp_path: Path) -> None:
    project = _project(tmp_path)
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    exit_code = main([str(project)])

    assert exit_code == 0
    report = json.loads(
        (project / ".factory" / "cleanup_report.json").read_text(encoding="utf-8")
    )
    assert report["executed"] is False
    assert report["policy_sha256"] == policy_fingerprint()
    assert scratch.is_file()


def test_policy_fingerprint_is_stable() -> None:
    assert policy_fingerprint() == policy_fingerprint()
    assert len(policy_fingerprint()) == 64


def test_plan_fails_closed_when_ownership_cannot_be_evaluated(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    import scripts.cleanup_project_artifacts as cleanup

    monkeypatch.setattr(
        cleanup, "authoritative_protected_paths", lambda _project: None
    )

    plan = cleanup.build_plan(project)
    assert plan.delete_dirs == []
    assert plan.delete_files == []

    _count, _bytes, _errors = cleanup.execute_plan(plan, project, set(), execute=True)
    assert scratch.is_file(), "unknown ownership must not authorise deletion"


def test_authoritative_final_input_is_protected(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    pinned = _write(project / "data" / "intermediate" / "pinned.csv")
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    import scripts.cleanup_project_artifacts as cleanup

    monkeypatch.setattr(
        cleanup,
        "authoritative_protected_paths",
        lambda _project: {"data/intermediate/pinned.csv"},
    )

    plan = cleanup.build_plan(project)
    assert plan.delete_dirs == [], "promoted input must block wholesale removal"
    _count, _bytes, _errors = cleanup.execute_plan(plan, project, set(), execute=True)

    assert pinned.is_file()
    assert not scratch.exists()


def test_execute_reverifies_with_plan_references(tmp_path: Path) -> None:
    """Removal must not run with a weaker veto than the plan was built with."""

    project = _project(tmp_path)
    scratch = _write(project / "data" / "intermediate" / "scratch.csv")

    plan = build_plan(project)
    assert scratch in plan.delete_files or (
        project / "data" / "intermediate"
    ) in plan.delete_dirs

    # Simulate protection discovered after planning: the re-check must honour it.
    plan.references = {"data/intermediate/scratch.csv"}
    _count, _bytes, had_errors = execute_plan(plan, project, set(), execute=True)

    assert had_errors is True
    assert scratch.is_file()
