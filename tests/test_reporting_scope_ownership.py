from factory_core.current_artifact_ownership import artifact_ownership
from factory_core.current_dirty import capture_artifact_manifest, classify_manifest_changes

def test_scope_review_is_final_input_and_reopens_final_audit(tmp_path):
    p=tmp_path/'models/reporting_scope/scope_review_manifest.json'
    p.parent.mkdir(parents=True)
    p.write_text('{"sources_sha256":{"paper.tex":"old"}}')
    before=capture_artifact_manifest(tmp_path)
    p.write_text('{"sources_sha256":{"paper.tex":"new"}}')
    changes=classify_manifest_changes(before,capture_artifact_manifest(tmp_path))
    assert [(c.owner_stage,c.flag.value) for c in changes]==[(10,'FORMAT_DIRTY')]
    role=artifact_ownership(p.relative_to(tmp_path).as_posix())
    assert role.final_input and role.submission_member

def test_scope_guard_and_model_changes_still_require_model_review():
    for path in ('models/reporting_scope/verify_reporting_scope.py',
                 'models/reporting_scope/quality_contract.candidate.json',
                 'models/A-S-04/code/release_q23.py','quality_contract.json'):
        owner=artifact_ownership(path)
        assert owner.owner_stage==3 and owner.dirty_flag=='MODEL_DIRTY'
