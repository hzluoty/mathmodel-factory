from pathlib import Path
import sys
from factory_core.adapters.infrastructure.commands import CommandRunner
from scripts.judge_packet import packet_fingerprints, _is_execution_evidence

def test_packaging_receipt_does_not_change_review_inputs(tmp_path):
    (tmp_path/"problem").mkdir()
    (tmp_path/"problem/source.md").write_text("Compute and explain a scalar result.\n")
    (tmp_path/"model.md").write_text("The declared model is y = x + 1.\n")
    (tmp_path/f"{tmp_path.name}_paper.tex").write_text("A report with the declared assumptions.\n")
    before=packet_fingerprints(tmp_path)
    result=CommandRunner().run(tmp_path,[sys.executable,"-c","print('complete package receipt')"],label="package_submission")
    assert result.accepted
    assert result.log_path.is_file()
    assert "complete package receipt" in result.log_path.read_text()
    assert not _is_execution_evidence(result.log_path.relative_to(tmp_path).as_posix())
    assert packet_fingerprints(tmp_path)==before
    solver=CommandRunner().run(tmp_path,[sys.executable,"-c","print('real solver evidence')"],label="solver")
    assert solver.accepted and _is_execution_evidence(solver.log_path.relative_to(tmp_path).as_posix())
    assert packet_fingerprints(tmp_path)["execution"]!=before["execution"]

def test_explicit_packaging_log_path_is_preserved(tmp_path):
    target=tmp_path/"logs/explicit-package-record.log"
    result=CommandRunner().run(tmp_path,[sys.executable,"-c","print('explicit record')"],label="package_submission",log_path=target)
    assert result.accepted and result.log_path==target
    assert "explicit record" in target.read_text()
