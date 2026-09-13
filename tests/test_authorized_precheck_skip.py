from types import SimpleNamespace

import pytest

from factory_core.domain import ExecutionResult, StepContext
from factory_core.steps.specialized import JudgeStep
from factory_core.steps.catalog import contract_for


@pytest.mark.parametrize("step_id,source,authorized,should_skip", [
    (13, "INDETERMINATE_REVIEW", True, True),
    (13, "OTHER_VERDICT", True, False),
    (13, "INDETERMINATE_REVIEW", False, False),
    (16, "INDETERMINATE_REVIEW", True, False),
])
def test_authorized_precheck_skip_is_scoped_and_does_not_rerun(tmp_path, monkeypatch, step_id, source, authorized, should_skip):
    original = "VERDICT: INDETERMINATE_REVIEW\nOriginal findings remain.\n"
    (tmp_path / "judge_evaluation.md").write_text(original)
    judge = JudgeStep(contract_for(13), tmp_path, None, None, None, None)
    override = SimpleNamespace(source_verdict=source, override_id="approved-id") if authorized else None
    monkeypatch.setattr(judge, "_continuation_override", lambda _: override)
    monkeypatch.setattr(judge, "_record_delivery_override", lambda *args, **kwargs: authorized)
    prepared = []
    def prepare(context):
        prepared.append(context.step_id)
        return ExecutionResult.failed("PACKET_UNAVAILABLE", returncode=2)
    monkeypatch.setattr(judge, "prepare_packets", prepare)
    monkeypatch.setattr(judge, "_continue_after_failure", lambda *args, **kwargs: None)
    result = judge.execute(StepContext(tmp_path, tmp_path.name, step_id, 1, 3600, 0))
    assert (tmp_path / "judge_evaluation.md").read_text() == original
    if should_skip:
        assert prepared == []
        assert result.returncode == 0
        assert result.metadata["precheck_skipped"] is True
        assert result.metadata["judge_completed"] is False
        assert result.metadata["delivery_allowed"] is False
        assert result.metadata["judge_verdict"] == "INDETERMINATE_REVIEW"
    else:
        assert prepared == [step_id]
        assert result.returncode == 2
