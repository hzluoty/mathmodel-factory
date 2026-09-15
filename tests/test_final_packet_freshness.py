from pathlib import Path

from factory_core.audit import FinalAuditService, AuditStatus
from tests.test_audit_service import RecordingRunner, PassingJudge, FakeValidator, make_context


def test_cache_miss_rebuilds_packets_after_current_acceptance_checks(tmp_path):
    root = tmp_path / 'factory'
    project = root / 'ongoing/demo'
    project.mkdir(parents=True)
    context = make_context(project)
    (project / f'{project.name}_paper.pdf').write_bytes(b'%PDF old candidate')
    report = project / 'quality-report.txt'
    report.write_text('OLD FAIL')

    class UpdatingRunner(RecordingRunner):
        def python(self, root, project, script, args, *, label, **kwargs):
            if label == 'audit_paper_quality_contract':
                report.write_text('CURRENT PASS')
            return super().python(root, project, script, args, label=label, **kwargs)

    class SnapshotJudge(PassingJudge):
        def __init__(self):
            super().__init__()
            self.observed = []

        def prepare_packets(self, context):
            self.observed.append(report.read_text())
            return super().prepare_packets(context)

        def execute_prepared(self, context):
            assert self.observed[-1] == 'CURRENT PASS'
            return super().execute_prepared(context)

    judge = SnapshotJudge()
    service = FinalAuditService(root, judge, FakeValidator(), UpdatingRunner(),
                               fingerprinter=lambda p, b: 'f' * 64)
    result = service.run(context)
    assert result.record.status is AuditStatus.PASS
    assert judge.observed == ['OLD FAIL', 'CURRENT PASS']
    assert judge.judge_calls == 1
