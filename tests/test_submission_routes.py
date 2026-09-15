import hashlib
import json

import pytest

from factory_core.submission_routes import declared_submission_routes, register_submission_route
from factory_core.submission_bundle import submission_bundle_manifest, submission_bundle_paths


def fixture(project):
    (project / 'docs').mkdir()
    (project / 'work').mkdir()
    source = project / 'docs/source-index.json'
    source.write_text('{"source":"original archived data"}\n')
    (project / 'work/review.md').write_text('Reviewed source index used by historical Solver input declaration.\n')
    (project / f'{project.name}_paper.tex').write_text('\\begin{document}ok\\end{document}\n')
    return source, dict(relative_path='docs/source-index.json', expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                        owner_stage=4, semantic_domain='solver_evidence', review_path='work/review.md', reason='Include exact reviewed source index')


def test_explicit_route_includes_file_and_binding_with_owner(tmp_path):
    source, args = fixture(tmp_path)
    assert source not in submission_bundle_paths(tmp_path, require_pdf=False)
    binding = register_submission_route(tmp_path, **args)
    paths = submission_bundle_paths(tmp_path, require_pdf=False)
    assert source in paths and binding in paths
    manifest = submission_bundle_manifest(tmp_path, require_pdf=False)
    item = next(v for v in manifest['members'] if v['source_path'] == args['relative_path'])
    assert item['owner_stage'] == 4 and item['semantic_domain'] == 'solver_evidence'


def test_route_refuses_unreviewed_bytes_and_future_changes(tmp_path):
    source, args = fixture(tmp_path)
    with pytest.raises(ValueError, match='reviewed file hash mismatch'):
        register_submission_route(tmp_path, **dict(args, expected_sha256='0' * 64))
    register_submission_route(tmp_path, **args)
    source.write_text('changed')
    with pytest.raises(ValueError, match='current file drift'):
        declared_submission_routes(tmp_path)


def test_route_evidence_is_append_only_and_review_bound(tmp_path):
    _, args = fixture(tmp_path)
    binding = register_submission_route(tmp_path, **args)
    assert register_submission_route(tmp_path, **args) == binding
    with pytest.raises(ValueError, match='append-only'):
        register_submission_route(tmp_path, **dict(args, reason='different reason'))
    review = tmp_path / json.loads(binding.read_text())['review']['path']
    review.chmod(0o644)
    review.write_text('changed')
    with pytest.raises(ValueError, match='review identity mismatch'):
        declared_submission_routes(tmp_path)


def test_route_cannot_exclude_files_or_use_invalid_owner(tmp_path):
    _, args = fixture(tmp_path)
    with pytest.raises(ValueError, match='requires an owner'):
        register_submission_route(tmp_path, **dict(args, owner_stage=0))
    binding = register_submission_route(tmp_path, **args)
    value = json.loads(binding.read_text())
    assert value['scope'] == 'include_in_final_input_and_submission'
    assert 'exclude' not in value


def test_missing_route_tree_cannot_hide_a_dangling_symlink(tmp_path):
    (tmp_path / '.factory').mkdir()
    (tmp_path / '.factory/finalization').symlink_to(tmp_path / 'missing', target_is_directory=True)
    with pytest.raises(ValueError, match='directory is unsafe'):
        declared_submission_routes(tmp_path)
