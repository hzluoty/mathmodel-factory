"""Start one new final-audit execution after an audited framework repair.

Ordinary resume keeps its existing attempt budget. This separate recovery
operation permits one attempt for a different evaluator contract, only when
every non-contract audit input is identical to a prior real PASS snapshot.
"""
import hashlib
import json
import re

from .domain import InvalidTransition, WorkflowStatus
from .final_judge_projection import verified_final_report
from scripts.submission_fingerprint import submission_fingerprint_payload


def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
                                     separators=(',',':')).encode()).hexdigest()


def retry_changed_final_contract(store, *, expected_revision):
    state=store.load()
    if (state.revision != expected_revision or state.status is not WorkflowStatus.FAILED
            or state.active_step != 16 or state.active_stage != 10
            or state.active_subtask != 'delivery' or state.runner_pid is not None
            or state.pending_action is not None or state.attempt < 1):
        raise InvalidTransition('changed-contract retry requires an idle failed final delivery')
    project=store.project_dir
    old=json.loads((project/'.factory/audits/latest.json').read_text())
    if (old.get('status') != 'PASS' or old.get('override') is not False
            or old.get('judge_completed') is not True or old.get('delivery_allowed') is not True
            or old.get('profile') != 'final'):
        raise InvalidTransition('changed-contract retry requires a previous real final PASS')
    if not re.fullmatch(r'[0-9a-f]{64}', str(old.get('snapshot_id') or '')):
        raise InvalidTransition('previous final snapshot identifier is invalid')
    proof=verified_final_report(project)
    if proof is None or proof['snapshot_id'] != old['snapshot_id']:
        raise InvalidTransition('previous final PASS acceptance chain is invalid')
    snapshot=json.loads((project/'.factory/audits'/old['snapshot_id']/'snapshot.json').read_text())
    before=snapshot['identity']
    current=submission_fingerprint_payload(project,policy_mode='enforce')
    if _hash(before) != old['snapshot_id']:
        raise InvalidTransition('previous final snapshot identity is invalid')
    previous_contract=before.get('evaluator_contract');new_contract=current.get('evaluator_contract')
    if not previous_contract or not new_contract or previous_contract==new_contract:
        raise InvalidTransition('the evaluator contract has not changed')
    if ({k:v for k,v in before.items() if k!='evaluator_contract'}
            != {k:v for k,v in current.items() if k!='evaluator_contract'}):
        raise InvalidTransition('authored or evidence inputs changed beyond the evaluator contract')
    new_hash=_hash(new_contract)
    if any(event.type=='FINAL_AUDIT_CONTRACT_RETRY_PREPARED'
           and event.payload.get('new_evaluator_contract_sha256')==new_hash
           for event in store.events()):
        raise InvalidTransition('the new evaluator contract already received its one retry')
    return store.transition(
        expected_revision=expected_revision,
        event_type='FINAL_AUDIT_CONTRACT_RETRY_PREPARED',
        changes={'status':WorkflowStatus.READY,'attempt':0},
        payload={
            'schema_version':'factory-final-contract-retry-v1',
            'reason':'A repaired evaluator contract requires a fresh final audit of unchanged scientific inputs',
            'previous_snapshot':old['snapshot_id'],
            'new_input_fingerprint':_hash(current),
            'old_evaluator_contract_sha256':_hash(previous_contract),
            'new_evaluator_contract_sha256':new_hash,
            'non_contract_inputs_unchanged':True,
            'previous_attempt':state.attempt,'additional_attempts':1,
            'quality_override':False,'judge_completed':False,'delivery_allowed':False,
        })
