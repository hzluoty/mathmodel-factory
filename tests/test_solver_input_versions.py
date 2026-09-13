import hashlib
import json

import pytest

from factory_core.finalization import build_final_input_manifest, verify_final_input_snapshot
from factory_core.solver_input_coverage import solver_declared_input_coverage
from factory_core.solver_input_versions import register_solver_input_version
from factory_core.submission_bundle import submission_bundle_paths
from scripts.solver_job_receipt import build_submission_receipt, receipt_paths, write_receipt


def fixture(project):
    (project / 'models').mkdir()
    script = project / 'models/solve.py'
    script.write_text('print(1)\n')
    source = project / 'models/config.json'
    source.write_bytes(b'{"a":1}\n')
    (project / 'work').mkdir()
    old = project / 'work/old-config.json'
    old.write_bytes(source.read_bytes())
    receipt = build_submission_receipt(
        project_dir=project, job_id='version-test', backend='local', runtime='python',
        script=script, workdir=script.parent, argv=(), max_time_seconds=30,
        requested_at=1, input_paths=(source,), output_paths=(project / 'result.json',), seeds=(1,),
    )
    receipt_path, _ = receipt_paths(project / '.factory/solver_receipts', 'version-test')
    write_receipt(receipt_path, receipt)
    original_receipt = receipt_path.read_bytes()
    source.write_bytes(b'{"a":1,"source_note":"reviewed metadata revision"}\n')
    review = project / 'work/review.md'
    review.write_text('Compared both versions: a=1 unchanged; only source_note added.\n')
    record = receipt['inputs'][0]
    args = dict(relative_path='models/config.json', historical_path='work/old-config.json',
                input_sha256=record['sha256'], input_size=record['size'],
                current_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                review_path='work/review.md', reason='Reviewed metadata-only change')
    return source, old, receipt_path, original_receipt, args


def test_version_binding_keeps_exact_old_and_current_inputs_in_final_bundle(tmp_path):
    source, old, receipt_path, original_receipt, args = fixture(tmp_path)
    with pytest.raises(ValueError, match='solver input size drift'):
        solver_declared_input_coverage(tmp_path)
    binding = register_solver_input_version(tmp_path, **args)
    coverage = solver_declared_input_coverage(tmp_path)
    assert source in coverage.included_paths
    assert binding in coverage.versioned_paths
    historical = [p for p in coverage.versioned_paths if p.suffix == '.bin']
    assert len(historical) == 1 and historical[0].read_bytes() == old.read_bytes()
    assert receipt_path.read_bytes() == original_receipt
    (tmp_path / f'{tmp_path.name}_paper.tex').write_text('\\begin{document}ok\\end{document}\n')
    selected = submission_bundle_paths(tmp_path, require_pdf=False)
    assert set(coverage.versioned_paths).issubset(selected)
    snapshot = build_final_input_manifest(tmp_path)
    verify_final_input_snapshot(tmp_path, snapshot)
    assert json.loads(binding.read_text())['solver_success_or_quality_approval'] is False


def test_wrong_historical_bytes_cannot_rebind_receipt(tmp_path):
    source, old, _, _, args = fixture(tmp_path)
    old.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match='historical solver input does not match'):
        register_solver_input_version(tmp_path, **args)


def test_unreviewed_current_edit_still_fails_after_registration(tmp_path):
    source, _, _, _, args = fixture(tmp_path)
    register_solver_input_version(tmp_path, **args)
    source.write_text('{"a":2}\n')
    with pytest.raises(ValueError, match='solver input (size|content) drift'):
        solver_declared_input_coverage(tmp_path)


@pytest.mark.parametrize('field', ['historical', 'review'])
def test_archived_evidence_tampering_is_rejected(tmp_path, field):
    _, _, _, _, args = fixture(tmp_path)
    binding = register_solver_input_version(tmp_path, **args)
    target = tmp_path / json.loads(binding.read_text())[field]['path']
    target.chmod(0o644)
    target.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='evidence identity mismatch'):
        solver_declared_input_coverage(tmp_path)


def test_binding_is_append_only_and_binds_reviewed_current_hash(tmp_path):
    source, _, _, _, args = fixture(tmp_path)
    wrong = dict(args, current_sha256='0' * 64)
    with pytest.raises(ValueError, match='reviewed current solver input hash mismatch'):
        register_solver_input_version(tmp_path, **wrong)
    binding = register_solver_input_version(tmp_path, **args)
    assert register_solver_input_version(tmp_path, **args) == binding
    with pytest.raises(ValueError, match='append-only'):
        register_solver_input_version(tmp_path, **dict(args, reason='different review'))
    binding.chmod(0o644)
    value = json.loads(binding.read_text())
    value['reason'] = 'modified'
    binding.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='binding content hash mismatch'):
        solver_declared_input_coverage(tmp_path)


def test_symlink_archive_is_rejected(tmp_path):
    source, _, _, _, args = fixture(tmp_path)
    binding = register_solver_input_version(tmp_path, **args)
    target = tmp_path / json.loads(binding.read_text())['historical']['path']
    target.chmod(0o644)
    target.unlink()
    target.symlink_to(source)
    with pytest.raises(ValueError, match='symlink'):
        solver_declared_input_coverage(tmp_path)
