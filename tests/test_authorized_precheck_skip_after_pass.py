from types import SimpleNamespace
import pytest
from factory_core.domain import ExecutionResult, StepContext
from factory_core.steps.specialized import JudgeStep
from factory_core.steps.validators import NativeArtifactValidator
from factory_core.steps.catalog import contract_for

@pytest.mark.parametrize("source", ["PASS", "PRECHECK_PASS"])
@pytest.mark.parametrize("step_id,authorized", [(13, True), (13, False), (16, True)])
def test_explicit_skip_survives_prior_final_pass(tmp_path, monkeypatch, source, step_id, authorized):
    original=f"VERDICT: {source}\nActual prior review retained.\n"
    (tmp_path/"judge_evaluation.md").write_text(original)
    judge=JudgeStep(contract_for(step_id), tmp_path, None, None, None, None)
    override=SimpleNamespace(source_verdict=source, override_id="authorized-skip") if authorized else None
    monkeypatch.setattr(judge, "_continuation_override", lambda _:override)
    monkeypatch.setattr(judge, "_record_delivery_override", lambda *a,**k:authorized)
    calls=[]
    def prepare(ctx):
        calls.append(ctx.step_id)
        return ExecutionResult.failed("PACKET_TEST_STOP",returncode=2)
    monkeypatch.setattr(judge,"prepare_packets",prepare)
    monkeypatch.setattr(judge,"_continue_after_failure",lambda *a,**k:None)
    result=judge.execute(StepContext(tmp_path,tmp_path.name,step_id,1,3600,0))
    if step_id==13 and authorized:
        assert calls==[]
        assert result.metadata["precheck_skipped"] is True
        assert result.metadata["judge_completed"] is False
        assert result.metadata["delivery_allowed"] is False
        assert result.metadata["judge_verdict"]==source
    else:
        assert calls==[step_id]
        assert result.returncode==2
    assert (tmp_path/"judge_evaluation.md").read_text()==original

@pytest.mark.parametrize("authorized", [False, True])
def test_skip_validation_stays_explicit_even_when_old_judge_passed(tmp_path, monkeypatch, authorized):
    (tmp_path/"judge_evaluation.md").write_text("VERDICT: PASS\n")
    monkeypatch.setattr("factory_core.steps.validators.gate2_continuation_override",lambda *a:authorized)
    ok,_,_,meta=NativeArtifactValidator(tmp_path,13)._step_13(tmp_path)
    assert ok
    if authorized:
        assert meta["gate2_continuation_override"] is True
        assert meta["judge_completed"] is False
        assert meta["delivery_allowed"] is False
    else:
        assert meta=={}
