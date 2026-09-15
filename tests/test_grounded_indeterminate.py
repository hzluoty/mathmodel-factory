import json
from pathlib import Path
import pytest
from factory_core.steps import validators


def setup(project):
    outputs=project/'judge_outputs';outputs.mkdir()
    records=[]
    for role in ['math','execution','paper']:
        status='INDETERMINATE' if role=='execution' else 'PASS'
        records.append({'role':role,'status':status,'verdict':status,'error':None})
        (outputs/f'{role}.grounding.json').write_text(json.dumps({'valid':True,'errors':[]}))
    (outputs/'aggregate.json').write_text(json.dumps({'schema_version':'judge-aggregate-v3','roles':records,'indeterminate_roles':['execution']}))
    (project/'judge_evaluation.md').write_text('VERDICT: INDETERMINATE_REVIEW\n')


def test_grounded_indeterminate_requires_new_evidence(tmp_path,monkeypatch):
    setup(tmp_path)
    monkeypatch.setattr(validators,'gate2_continuation_override',lambda *a:False)
    valid,_,_,metadata=validators.NativeArtifactValidator(tmp_path,13)._step_13(tmp_path)
    assert not valid and metadata['normalized_verdict']=='INDETERMINATE_REVIEW'
    assert metadata['retry_scope']=='new_evidence_required'


@pytest.mark.parametrize('failure',['missing','bad_quote','malformed','array','null'])
def test_invalid_grounding_still_gets_bounded_infrastructure_retry(tmp_path,monkeypatch,failure):
    setup(tmp_path)
    p=tmp_path/'judge_outputs/execution.grounding.json'
    if failure=='missing':p.unlink()
    elif failure=='bad_quote':p.write_text(json.dumps({'valid':False,'errors':['quote not unique']}))
    elif failure=='array':p.write_text('[]')
    elif failure=='null':p.write_text('null')
    else:p.write_text('invalid')
    monkeypatch.setattr(validators,'gate2_continuation_override',lambda *a:False)
    valid,_,_,metadata=validators.NativeArtifactValidator(tmp_path,13)._step_13(tmp_path)
    assert not valid and metadata['normalized_verdict']=='INFRA_RETRY'
