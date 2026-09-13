import hashlib
import json
from pathlib import Path

import pytest

from scripts import judge_packet as packet
from scripts.evidence_grounding import GroundingError, _context_sections, validate_grounding


def prepare(tmp_path, monkeypatch):
    raw = b'{"unique_label": "complete original numerical array",\r\n"values": [' + b'1,' * 220000 + b'2]}\r\n'
    (tmp_path / 'full.json').write_bytes(raw)
    (tmp_path / 'data.npz').write_bytes(b'opaque binary evidence\x00\x01')
    paths = ['full.json', 'data.npz']
    config = {'schema_version': 'judge-evidence-v1', 'roles': {'execution': {
        'required_assets': [{'path': p, 'sha256': hashlib.sha256((tmp_path/p).read_bytes()).hexdigest()} for p in paths]}}}
    (tmp_path/'judge_evidence.json').write_text(json.dumps(config))
    req = [{'id': 'complete assets', 'paths': paths}]
    context, files = packet._render_context(tmp_path, 'execution', [tmp_path/p for p in paths], req)
    assert len(context.encode()) < 1000
    assert packet._completeness(files, req)['eligible']
    manifest = {'role': 'execution', 'files': files, 'context': {'sha256': hashlib.sha256(context.encode()).hexdigest()}}
    monkeypatch.setattr(packet, 'packet_payloads', lambda *a, **k: {'execution': {'context': context, 'manifest': manifest}})
    packet.build_packets(tmp_path)
    return tmp_path/'judge_packets/execution', manifest, context


def test_full_assets_are_copied_and_quotes_bind_to_raw_text(tmp_path, monkeypatch):
    root, manifest, context = prepare(tmp_path, monkeypatch)
    text_item, binary_item = manifest['files']
    assert (root/text_item['asset_path']).read_bytes() == (tmp_path/'full.json').read_bytes()
    assert (root/binary_item['asset_path']).read_bytes() == (tmp_path/'data.npz').read_bytes()
    sections = _context_sections(context, manifest['files'], root)
    assert sections['full.json']['text'].encode() == (tmp_path/'full.json').read_bytes()
    quote = '"unique_label": "complete original numerical array",\r\n'
    output = {'schema_version':'judge-hard-role-v2','role':'execution','verdict':'PASS',
              'evidence':[{'ref_id':'e1','chunk_id':text_item['chunk_id'],'quote':quote}]}
    verdict = tmp_path/'execution.md'
    verdict.write_text('VERDICT: PASS\n'+json.dumps(output))
    report = validate_grounding(verdict,root/'manifest.json',role='execution')
    assert report['valid'], report
    assert report['refs'][0]['asset_path'] == text_item['asset_path']
    assert report['refs'][0]['context_line_start'] is None


@pytest.mark.parametrize('tamper', ['missing','modified','escape','symlink','binary_modified'])
def test_assets_fail_closed_even_when_not_cited(tmp_path, monkeypatch, tamper):
    root, manifest, context = prepare(tmp_path, monkeypatch)
    item = manifest['files'][1 if tamper == 'binary_modified' else 0]
    target = root/item['asset_path']
    if tamper == 'missing': target.unlink()
    elif tamper in {'modified','binary_modified'}: target.write_bytes(b'changed')
    elif tamper == 'escape': item['asset_path'] = '../math/full.json'
    elif tamper == 'symlink':
        target.unlink(); target.symlink_to(tmp_path/'full.json')
    with pytest.raises(GroundingError): _context_sections(context,manifest['files'],root)


def test_existing_asset_corruption_is_not_overwritten(tmp_path, monkeypatch):
    root, manifest, _ = prepare(tmp_path, monkeypatch)
    target = root/manifest['files'][0]['asset_path']; target.write_bytes(b'corrupted')
    with pytest.raises(ValueError): packet.build_packets(tmp_path)
    assert target.read_bytes() == b'corrupted'


@pytest.mark.parametrize('path', ['../outside.json', 'judge_outputs/math.md'])
def test_asset_sources_reject_escape_and_peer_verdicts(tmp_path, path):
    (tmp_path/'judge_outputs').mkdir()
    (tmp_path/'judge_outputs/math.md').write_text('VERDICT: PASS')
    (tmp_path/'judge_evidence.json').write_text(json.dumps({'schema_version':'judge-evidence-v1','roles':{'execution':{
        'required_assets':[{'path':path,'sha256':'0'*64}]}}}))
    with pytest.raises(ValueError): packet._asset_supplement(tmp_path,'execution')
