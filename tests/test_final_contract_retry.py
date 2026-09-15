import json
from types import SimpleNamespace
import pytest
from factory_core import final_contract_retry as mod
from factory_core.domain import InvalidTransition,WorkflowStatus


def fixture(tmp_path,monkeypatch,change=None,events=()):
    before={'evaluator_contract':{'version':'old'},'science':'unchanged'}
    sid=mod._hash(before)
    (tmp_path/'.factory/audits'/sid).mkdir(parents=True)
    old={'snapshot_id':sid,'status':'PASS','override':False,'judge_completed':True,'delivery_allowed':True,'profile':'final'}
    (tmp_path/'.factory/audits/latest.json').write_text(json.dumps(old))
    (tmp_path/'.factory/audits'/sid/'snapshot.json').write_text(json.dumps({'identity':before}))
    current=change if change is not None else {**before,'evaluator_contract':{'version':'new'}}
    monkeypatch.setattr(mod,'submission_fingerprint_payload',lambda *a,**kw:current)
    monkeypatch.setattr(mod,'verified_final_report',lambda *a,**kw:{'snapshot_id':sid})
    state=SimpleNamespace(revision=5,status=WorkflowStatus.FAILED,active_step=16,active_stage=10,
                          active_subtask='delivery',runner_pid=None,pending_action=None,attempt=1)
    store=SimpleNamespace(project_dir=tmp_path,load=lambda:state,events=lambda:events,transition=lambda **kw:kw)
    return store,current


def test_changed_contract_gets_one_new_attempt_without_success_or_baseline_write(tmp_path,monkeypatch):
    store,_=fixture(tmp_path,monkeypatch)
    result=mod.retry_changed_final_contract(store,expected_revision=5)
    assert result['changes']=={'status':WorkflowStatus.READY,'attempt':0}
    assert result['payload']['additional_attempts']==1
    assert result['payload']['judge_completed'] is False
    assert result['payload']['delivery_allowed'] is False
    assert result['payload']['quality_override'] is False


@pytest.mark.parametrize('current,reason',[
    ({'evaluator_contract':{'version':'old'},'science':'unchanged'},'has not changed'),
    ({'evaluator_contract':{'version':'new'},'science':'edited'},'beyond the evaluator'),
])
def test_same_contract_or_semantic_change_cannot_reset_budget(tmp_path,monkeypatch,current,reason):
    store,_=fixture(tmp_path,monkeypatch,change=current)
    with pytest.raises(InvalidTransition,match=reason):mod.retry_changed_final_contract(store,expected_revision=5)


def test_retry_cannot_repeat_for_same_new_contract(tmp_path,monkeypatch):
    store,current=fixture(tmp_path,monkeypatch)
    store.events=lambda:[SimpleNamespace(type='FINAL_AUDIT_CONTRACT_RETRY_PREPARED',payload={'new_evaluator_contract_sha256':mod._hash(current['evaluator_contract'])})]
    with pytest.raises(InvalidTransition,match='already received'):
        mod.retry_changed_final_contract(store,expected_revision=5)
