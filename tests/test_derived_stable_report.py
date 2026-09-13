import json

from scripts.verify_derived_artifacts import main
from tests.test_verify_derived_artifacts import setup_project, sha256


def test_cli_stable_result_preserves_actual_runtime_and_detects_diff(tmp_path, capsys):
    manifest, manifest_path, _, generator, actual_json, _ = setup_project(tmp_path)
    with generator.open('a') as handle:
        handle.write("\nimport time, sys\nprint(a.output_dir)\nprint(time.time_ns())\nprint('trace stderr', file=sys.stderr)\n")
    manifest['generator']['sha256'] = sha256(generator)
    manifest_path.write_text(json.dumps(manifest))
    target = tmp_path / 'verification.json'
    args = [str(tmp_path), '--manifest', str(manifest_path), '--json-out', str(target)]
    assert main(args) == 0
    first_bytes = target.read_bytes()
    first_log = capsys.readouterr().out
    assert main(args) == 0
    assert target.read_bytes() == first_bytes
    second_log = capsys.readouterr().out
    first = json.loads(first_log.splitlines()[0].split(': ', 1)[1])
    second = json.loads(second_log.splitlines()[0].split(': ', 1)[1])
    assert first['generator_command'] != second['generator_command']
    assert first['generator_stdout'] != second['generator_stdout']
    assert first['generator_stderr'] == 'trace stderr\n'
    stable = json.loads(first_bytes)
    for key in ['outputs', 'failures', 'passed', 'generator_returncode', 'manifest_sha256']:
        assert stable[key] == first[key] == second[key]
    actual_json.write_text('{"objective": 43}\n')
    assert main(args) == 1
    failed = json.loads(target.read_bytes())
    assert failed['passed'] is False
    assert 'ACTUAL_DIGEST_MISMATCH' in failed['failures']
    assert target.read_bytes() != first_bytes


def test_cli_generator_failure_retains_exit_code_and_full_error(tmp_path, capsys):
    manifest, manifest_path, _, generator, *_ = setup_project(tmp_path)
    generator.write_text("import sys\nprint('diagnostic',file=sys.stderr)\nsys.exit(7)\n")
    manifest['generator']['sha256'] = sha256(generator)
    manifest_path.write_text(json.dumps(manifest))
    target = tmp_path / 'verification.json'
    assert main([str(tmp_path), '--manifest', str(manifest_path), '--json-out', str(target)]) == 1
    raw = json.loads(capsys.readouterr().out.splitlines()[0].split(': ', 1)[1])
    stable = json.loads(target.read_bytes())
    assert stable['generator_returncode'] == 7
    assert stable['passed'] is False
    assert 'GENERATOR_FAILED' in stable['failures']
    assert raw['generator_stderr'] == 'diagnostic\n'
