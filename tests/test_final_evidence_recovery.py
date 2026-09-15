import json
import sqlite3

import pytest

from factory_core.current_dirty import capture_artifact_manifest, manifest_fingerprint, classifier_contract_sha256
from factory_core.dirty_rebase import rebase_dirty_classifier_state
from factory_core.domain import InvalidTransition, SCHEMA_VERSION, WorkflowStatus
from factory_core.final_evidence_recovery import recover_final_evidence_config
from factory_core.storage import SQLiteStateStore


def fixture(tmp_path):
    store = SQLiteStateStore(tmp_path)
    state = store.initialize(project_id="recovery-test", project_type="modeling")
    (tmp_path / "sample_paper.tex").write_text("unchanged paper")
    fp = manifest_fingerprint(capture_artifact_manifest(tmp_path))
    for step, stage, subtask, receipt in [
        (13, 8, "conditional_math_preflight", {"precheck_skipped": True, "judge_completed": False, "verdict": "INDETERMINATE_REVIEW"}),
        (15, 9, "polish", {"validated": True}),
    ]:
        state = store.transition(expected_revision=state.revision, event_type="TEST_CHECKPOINT",
            stage_checkpoint={"stage_id": stage, "subtask": subtask, "source_step_id": step,
                "completed_step_id": step, "input_fingerprint": fp, "output_fingerprint": fp,
                "receipt": receipt})
    source_revision = state.revision
    (tmp_path / "judge_evidence.json").write_text('{"schema_version":1}')
    sha = capture_artifact_manifest(tmp_path)["judge_evidence.json"]
    state = store.transition(expected_revision=state.revision, event_type="STAGE_SEMANTIC_REOPENED",
        invalidate_checkpoints_after_step=4,
        dirty_changes=[{"flag": flag, "owner_stage": owner, "cause_artifact": "judge_evidence.json",
            "baseline_fingerprint": "MISSING", "current_fingerprint": sha,
            "classifier_contract_sha256": "historical-unknown-classifier"}
            for flag, owner in [("MATH_DIRTY", 8), ("RESULT_DIRTY", 4)]])
    state = store.transition(expected_revision=state.revision, event_type="TEST_PAUSE",
                             changes={"status": WorkflowStatus.PAUSED})
    return store, state.revision, source_revision


def test_recovery_preserves_history_and_skipped_review(tmp_path):
    store, revision, source = fixture(tmp_path)
    state = recover_final_evidence_config(store, expected_revision=revision, source_revision=source)
    assert state.status is WorkflowStatus.READY and state.active_step == 16
    with store._session() as c:
        assert c.execute("SELECT COUNT(*) FROM stage_checkpoint_history").fetchone()[0] == 2
        receipt = json.loads(c.execute("SELECT receipt_json FROM stage_checkpoints WHERE source_step_id=13").fetchone()[0])
        assert receipt == {"precheck_skipped": True, "judge_completed": False, "verdict": "INDETERMINATE_REVIEW"}
        rebase_dirty_classifier_state(c, source_schema_version=SCHEMA_VERSION, target_schema_version=SCHEMA_VERSION)
        dirty = [dict(r) for r in c.execute("SELECT * FROM dirty_flags")]
        assert [(r["flag"], r["owner_stage"]) for r in dirty] == [("FORMAT_DIRTY", 10)]
        assert c.execute("SELECT COUNT(*) FROM dirty_causes").fetchone()[0] == 3
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM dirty_classifier_rebases")
    assert store.verify_aggregate_domain_root()
    from factory_core.final_evidence_recovery import classifier_equivalent_for_restored_checkpoints
    checkpoints = [c for c in store.stage_checkpoints() if c["stage_id"] == 9]
    assert classifier_equivalent_for_restored_checkpoints(store, checkpoints, classifier_contract_sha256())
    state = store.transition(expected_revision=state.revision, event_type="STAGE_CHECKPOINT_INVALIDATED",
                             changes={"status": WorkflowStatus.PAUSED}, invalidate_checkpoints_after_step=12)
    state = recover_final_evidence_config(store, expected_revision=state.revision, source_revision=source)
    assert state.active_step == 16 and len(store.stage_checkpoints()) == 2
    (tmp_path / "sample_paper.tex").write_text("drift")
    assert not classifier_equivalent_for_restored_checkpoints(store, checkpoints, classifier_contract_sha256())


@pytest.mark.parametrize("tamper", ["paper", "config", "source", "running", "checkpoint"])
def test_recovery_fails_closed_on_drift(tmp_path, tamper):
    store, revision, source = fixture(tmp_path)
    if tamper == "paper":
        (tmp_path / "sample_paper.tex").write_text("changed paper")
    elif tamper == "config":
        (tmp_path / "judge_evidence.json").write_text("changed config")
    elif tamper == "source":
        source -= 1
    elif tamper == "running":
        revision = store.transition(expected_revision=revision, event_type="TEST_RUN",
            changes={"status": WorkflowStatus.RUNNING}).revision
    elif tamper == "checkpoint":
        revision = store.transition(expected_revision=revision, event_type="TEST_NEW",
            stage_checkpoint={"stage_id": 10, "subtask": "final", "source_step_id": 16,
                "completed_step_id": 16, "input_fingerprint": "new", "output_fingerprint": "new"}).revision
    with pytest.raises(InvalidTransition):
        recover_final_evidence_config(store, expected_revision=revision, source_revision=source)
    assert store.load().revision == revision


def test_requested_run_scope_stops_before_execution(tmp_path):
    from test_stage_scheduler import stage_registry
    from factory_core.engine import FactoryEngine
    from factory_core.stages import STAGE_SCHEDULER_GENERATION
    registry, lifecycles = stage_registry()
    store = SQLiteStateStore(tmp_path)
    store.initialize(project_id="bounded", project_type="modeling", scheduler_generation=STAGE_SCHEDULER_GENERATION)
    state = FactoryEngine(tmp_path, store=store, registry=registry).run(allowed_source_steps=frozenset({16}))
    assert state.status is WorkflowStatus.PAUSED
    assert not any(event.type == "STEP_STARTED" for event in store.events())
    assert store.events()[-1].type == "RUN_BOUNDARY_REACHED"
