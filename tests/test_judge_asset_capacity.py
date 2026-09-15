import hashlib,json
import pytest
from scripts import judge_packet as packet

def test_default_capacity_unchanged():
    assert packet._asset_limits({})==(256_000_000,512_000_000)
    assert packet._asset_limits({'asset_max_file_bytes':400_000_000,'asset_total_bytes':1_000_000_000})==(400_000_000,1_000_000_000)

@pytest.mark.parametrize('file,total',[(True,512_000_000),(256_000_000,True),(0,512_000_000),(-1,512_000_000),(512_000_001,1_000_000_000),(400_000_000,399_999_999),(400_000_000,2_000_000_001),('400000000',1_000_000_000)])
def test_invalid_capacity_rejected(file,total):
    with pytest.raises(ValueError,match='asset budget'):
        packet._asset_limits({'asset_max_file_bytes':file,'asset_total_bytes':total})

def fixture_config(tmp_path,per_file,total):
    assets=[]
    for n in ('one.npz','two.npz'):
        data=(n.encode()*30)[:100];(tmp_path/n).write_bytes(data)
        assets.append({'path':n,'sha256':hashlib.sha256(data).hexdigest()})
    cfg={'schema_version':'judge-evidence-v1','roles':{'execution':{'required_assets':assets,'asset_max_file_bytes':per_file,'asset_total_bytes':total}}}
    (tmp_path/'judge_evidence.json').write_text(json.dumps(cfg))

def test_explicit_capacity_is_enforced(tmp_path):
    fixture_config(tmp_path,100,200)
    assert len(packet._asset_supplement(tmp_path,'execution'))==2
    fixture_config(tmp_path,99,200)
    with pytest.raises(ValueError,match='byte limit'):packet._asset_supplement(tmp_path,'execution')
    fixture_config(tmp_path,100,199)
    with pytest.raises(ValueError,match='byte limit'):packet._asset_supplement(tmp_path,'execution')

def test_capacity_never_bypasses_digest_check(tmp_path):
    fixture_config(tmp_path,400_000_000,1_000_000_000)
    (tmp_path/'one.npz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash drift'):packet._asset_supplement(tmp_path,'execution')
