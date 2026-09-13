import hashlib
import json
import pytest
from scripts import judge_packet as packet
from scripts.evidence_grounding import _context_sections, GroundingError


@pytest.mark.parametrize('suffix', ['.pdf', '.zip'])
def test_hash_pinned_opaque_source_assets_are_copied_without_unpacking(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('evidence' + suffix)
    raw = b'opaque exact source bytes\x00\x01'
    path.write_bytes(raw)
    config = {'schema_version':'judge-evidence-v1','roles':{'execution':{
        'required_assets':[{'path':path.name,'sha256':hashlib.sha256(raw).hexdigest()}]}}}
    (tmp_path/'judge_evidence.json').write_text(json.dumps(config))
    requirements = [{'id':'pinned opaque source','paths':[path.name]}]
    context, files = packet._render_context(tmp_path,'execution',[path],requirements)
    assert packet._completeness(files, requirements)['eligible']
    item = files[0]
    assert item['asset_quote_mode'] == 'descriptor'
    manifest = {'role':'execution','files':files,'context':{'sha256':hashlib.sha256(context.encode()).hexdigest()}}
    monkeypatch.setattr(packet,'packet_payloads',lambda *a,**k:{'execution':{'context':context,'manifest':manifest}})
    packet.build_packets(tmp_path)
    root=tmp_path/'judge_packets/execution'
    target=root/item['asset_path']
    assert target.read_bytes()==raw
    _context_sections(context, files, root)
    target.write_bytes(b'modified')
    with pytest.raises(GroundingError):_context_sections(context, files, root)
