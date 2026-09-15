import importlib.util
import os
from pathlib import Path
import pytest

@pytest.fixture
def api():
    # Validate the staged change against the production package dependencies.
    staged=os.environ.get('ARTIFACT_API_CANDIDATE')
    if not staged:
        from web.backend import project_api
        return project_api
    spec=importlib.util.spec_from_file_location('web.backend.artifact_hotfix_candidate',staged)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

@pytest.fixture
def project(tmp_path):
    root=tmp_path/'actual'/'sample';root.mkdir(parents=True)
    (root/'sample_paper.tex').write_text('\\documentclass{article}\n\\begin{document}\n\\input{paper/body}\n\\end{document}\n')
    (root/'paper').mkdir();(root/'paper/body.tex').write_text('Contents')
    (root/'sample_paper.pdf').write_bytes(b'%PDF-1.4\nfixture')
    (root/'results').mkdir();(root/'results/summary.json').write_text('{}')
    return root

def test_symlink_project_matches_direct_paths(api,project,tmp_path):
    alias=tmp_path/'visible'/'sample';alias.parent.mkdir();alias.symlink_to(project,target_is_directory=True)
    actual=api.list_artifacts(alias)
    assert actual==api.list_artifacts(project)
    paths={row['path'] for row in actual}
    assert {'sample_paper.tex','sample_paper.pdf','paper/body.tex','results/summary.json'}<=paths
    for row in actual:
        assert api._safe_path(alias,row['path']).is_file()
        assert not Path(row['path']).is_absolute()

def test_relative_project_matches_absolute(api,project,monkeypatch):
    monkeypatch.chdir(project.parent)
    assert api.list_artifacts(Path(project.name))==api.list_artifacts(project)

def test_external_symlink_is_not_listed_or_downloadable(api,project,tmp_path):
    outside=tmp_path/'private.json';outside.write_text('{"private":true}')
    (project/'results/outside.json').symlink_to(outside)
    assert 'results/outside.json' not in {row['path'] for row in api.list_artifacts(project)}
    with pytest.raises(api.HTTPException) as exc:api._safe_path(project,'results/outside.json')
    assert exc.value.status_code==400

def test_internal_alias_deduplicates(api,project):
    (project/'results/copy.json').symlink_to(project/'results/summary.json')
    rows=api.list_artifacts(project)
    assert sum(row['path']=='results/summary.json' for row in rows)==1
    assert all(row['path']!='results/copy.json' for row in rows)

def test_plain_project_and_hidden_files(api,project):
    (project/'results/.git').mkdir();(project/'results/.git/private.json').write_text('{}')
    rows=api.list_artifacts(project)
    assert any(row['path']=='results/summary.json' for row in rows)
    assert not any('.git' in Path(row['path']).parts for row in rows)
