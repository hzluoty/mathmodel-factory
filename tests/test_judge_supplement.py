import hashlib
import json
import pytest
from scripts.judge_packet import _supplement, _render_context, _completeness

def config(p, path='evidence.txt', digest=None, limit=400000):
    content = p / 'evidence.txt'
    if not content.exists():
        content.write_text('x' * 80000)
    (p/'judge_evidence.json').write_text(json.dumps({'schema_version':'judge-evidence-v1','roles':{'math':{
        'context_bytes':limit, 'required_files':[{'path':path, 'sha256':digest or hashlib.sha256(content.read_bytes()).hexdigest()}]}}}))

def test_default_unchanged(tmp_path):
    assert _supplement(tmp_path, 'math') == (180000, [])
    assert _supplement(tmp_path, 'execution') == (360000, [])

def test_required_large_file_complete_and_overflow_incomplete(tmp_path):
    config(tmp_path)
    limit, paths = _supplement(tmp_path, 'math')
    req = [{'id':'evidence','paths':paths}]
    text, files = _render_context(tmp_path,'math',[tmp_path/p for p in paths],req)
    assert limit == 400000 and _completeness(files,req)['eligible']
    assert 'middle truncated' not in text and 'x'*80000 in text
    (tmp_path/'evidence.txt').write_text('x'*410000)
    config(tmp_path)
    _, files = _render_context(tmp_path,'math',[tmp_path/p for p in paths],req)
    assert not _completeness(files,req)['eligible']

@pytest.mark.parametrize('path,digest,limit', [('../outside',None,400000),('/etc/passwd',None,400000),
    ('evidence.txt','0'*64,400000),('evidence.txt',None,179999),('evidence.txt',None,2000001)])
def test_invalid_inputs_fail_closed(tmp_path,path,digest,limit):
    config(tmp_path,path,digest,limit)
    with pytest.raises(ValueError):
        _supplement(tmp_path,'math')

def test_symlink_and_verdict_rejected(tmp_path):
    (tmp_path/'judge_evaluation.md').write_text('PASS')
    config(tmp_path,'judge_evaluation.md')
    with pytest.raises(ValueError): _supplement(tmp_path,'math')
    (tmp_path/'link.txt').symlink_to(tmp_path/'evidence.txt')
    config(tmp_path,'link.txt')
    with pytest.raises(ValueError): _supplement(tmp_path,'math')
