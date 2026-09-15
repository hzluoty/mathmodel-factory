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
    manifest = {'role': 'execution', 'files': files, 'context': {'sha256': hashlib.sha256(context.encode()).hexdigest(), 'size': len(context.encode())}}
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


def test_role_assets_bind_to_native_batch_and_reject_tampering(tmp_path, monkeypatch):
    from factory_core import judge_batch
    root, manifest, _ = prepare(tmp_path, monkeypatch)
    for role in ("paper", "math"):
        folder = tmp_path / "judge_packets" / role
        folder.mkdir()
        (folder / "context.txt").write_text("")
        (folder / "manifest.json").write_text(json.dumps({"role": role, "files": []}))
    (tmp_path / "judge_packets/objective_evidence.json").write_text("{}")
    monkeypatch.setattr("scripts.submission_fingerprint.evaluator_contract_payload", lambda *a, **k: {})
    descriptor = judge_batch.descriptor(tmp_path, tmp_path, 16, "execution", "review")
    for item in manifest["files"]:
        name = "judge_packets/execution/" + item["asset_path"]
        assert descriptor["inputs"][name] == {"bytes": item["asset_size"], "sha256": item["asset_sha256"]}
    (root / manifest["files"][0]["asset_path"]).write_bytes(b"changed")
    with pytest.raises(judge_batch.JudgeBatchError, match="asset changed"):
        judge_batch.descriptor(tmp_path, tmp_path, 16, "execution", "review")


def test_in_memory_role_asset_grounding_requires_exact_bytes(tmp_path, monkeypatch):
    from scripts.evidence_grounding import validate_grounding_bytes
    root, manifest, context = prepare(tmp_path, monkeypatch)
    item = manifest["files"][0]
    output = ("VERDICT: PASS\n" + json.dumps({
        "schema_version": "judge-hard-role-v2", "role": "execution", "verdict": "PASS",
        "evidence": [{"ref_id": "e1", "chunk_id": item["chunk_id"], "quote": "complete original numerical array"}],
    })).encode()
    assets = {entry["asset_path"]: (root / entry["asset_path"]).read_bytes() for entry in manifest["files"]}
    report = validate_grounding_bytes(output, json.dumps(manifest).encode(), context.encode(), role="execution", assets=assets)
    assert report["valid"], report
    assets[item["asset_path"]] = b"changed"
    report = validate_grounding_bytes(output, json.dumps(manifest).encode(), context.encode(), role="execution", assets=assets)
    assert not report["valid"]


@pytest.mark.parametrize('component', ['role', 'packet_parent'])
def test_role_directory_links_are_rejected_even_with_matching_bytes(tmp_path, monkeypatch, component):
    root, manifest, context = prepare(tmp_path, monkeypatch)
    linked = root if component == 'role' else root.parent
    outside = tmp_path / 'outside-packet'
    linked.rename(outside)
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(GroundingError) as exc:
        _context_sections(context, manifest['files'], root)
    assert exc.value.code == 'ASSET_UNREADABLE'


def test_filesystem_grounding_preserves_original_role_directory_boundary(tmp_path, monkeypatch):
    root, manifest, _ = prepare(tmp_path, monkeypatch)
    item = manifest['files'][0]
    output = tmp_path / 'execution.md'
    output.write_text('VERDICT: PASS\n' + json.dumps({
        'schema_version': 'judge-hard-role-v2', 'role': 'execution', 'verdict': 'PASS',
        'evidence': [{'ref_id': 'e1', 'chunk_id': item['chunk_id'],
                      'quote': 'complete original numerical array'}],
    }))
    assert validate_grounding(output, root / 'manifest.json', role='execution')['valid']
    outside = tmp_path / 'outside-role'
    root.rename(outside)
    root.symlink_to(outside, target_is_directory=True)
    report = validate_grounding(output, root / 'manifest.json', role='execution')
    assert not report['valid']
    assert any(error['code'] == 'ASSET_UNREADABLE' for error in report['errors'])


def test_phase7_preserves_verified_text_asset_reference_coordinates(tmp_path, monkeypatch):
    from scripts.evidence_grounding import validate_grounding_bytes
    from factory_core.phase7_grounding_runtime import _normalized_report

    root, manifest, context = prepare(tmp_path, monkeypatch)
    item = manifest['files'][0]
    output = ('VERDICT: PASS\n' + json.dumps({
        'schema_version': 'judge-hard-role-v2', 'role': 'execution', 'verdict': 'PASS',
        'evidence': [{'ref_id': 'e1', 'chunk_id': item['chunk_id'],
                      'quote': 'complete original numerical array'}],
    })).encode()
    manifest_bytes = json.dumps(manifest).encode()
    assets = {entry['asset_path']: (root / entry['asset_path']).read_bytes() for entry in manifest['files']}
    report = validate_grounding_bytes(output, manifest_bytes, context.encode(), role='execution', assets=assets)
    assert report['valid']
    normalized = _normalized_report('execution', report, manifest_bytes, context.encode())
    assert normalized['refs'][0]['asset_path'] == item['asset_path']
    assert normalized['refs'][0]['context_line_start'] is None
    assert normalized['refs'][0]['source_line_start'] >= 1
