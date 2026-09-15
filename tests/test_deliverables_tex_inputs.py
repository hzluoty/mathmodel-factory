import json

import pytest
from scripts import verify_deliverables as checker


def setup_project(tmp_path, files, fields=('航向角','速度','投放时刻')):
    (tmp_path/'problem').mkdir()
    (tmp_path/'problem/deliverables.json').write_text(json.dumps({'attachments':[], 'strategy_tables':[{'problem':'Q1','fields':list(fields)}]}))
    for name, text in files.items():
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text)


def run_check(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv',['verify_deliverables.py',str(tmp_path),'demo'])
    return checker.main()


def test_nested_input_is_checked_with_existing_field_rule(tmp_path, monkeypatch, capsys):
    setup_project(tmp_path, {'demo_paper.tex':r'\begin{document}\input{part}\end{document}', 'part.tex':r'\input{table}', 'table.tex':r'\begin{tabular}{ll}航向角 & 速度\end{tabular}'})
    assert run_check(tmp_path,monkeypatch)==0
    assert 'TABLES_MISSING=0' in capsys.readouterr().out


@pytest.mark.parametrize('files', [
    {'demo_paper.tex':r'\begin{document}\input{missing}\end{document}'},
    {'demo_paper.tex':r'\begin{document}\input{part}\end{document}', 'part.tex':r'\input{demo_paper}'},
    {'demo_paper.tex':r'\begin{document}\input{../outside}\end{document}'},
])
def test_invalid_dependency_fails_closed(tmp_path,monkeypatch,capsys,files):
    setup_project(tmp_path,files)
    assert run_check(tmp_path,monkeypatch)==1
    assert 'VERDICT: FAIL' in capsys.readouterr().out


def test_insufficient_fields_and_unreferenced_draft_still_fail(tmp_path,monkeypatch,capsys):
    setup_project(tmp_path, {'demo_paper.tex':r'\begin{document}\input{table}\end{document}', 'table.tex':r'\begin{tabular}{l}航向角\end{tabular}', 'unused.tex':r'\begin{tabular}{lll}航向角 & 速度 & 投放时刻\end{tabular}'})
    assert run_check(tmp_path,monkeypatch)==1
    assert 'TABLES_MISSING=1' in capsys.readouterr().out
