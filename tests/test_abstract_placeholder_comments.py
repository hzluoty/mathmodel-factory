from pathlib import Path

import pytest

from factory_core.paper_sources import count_abstract_placeholders
from factory_core.steps.validators import NativeArtifactValidator


@pytest.mark.parametrize('text,expected', [
    ('% ABSTRACT_PLACEHOLDER\nReal abstract', 0),
    ('% \\AbstractPlaceholder\nReal abstract', 0),
    ('ABSTRACT_PLACEHOLDER', 1),
    ('\\AbstractPlaceholder', 1),
    ('Text \\% ABSTRACT_PLACEHOLDER', 1),
    ('% old marker\n\\AbstractPlaceholder\n% ABSTRACT_PLACEHOLDER', 1),
])
def test_only_active_abstract_placeholders_are_counted(text, expected):
    assert count_abstract_placeholders(text) == expected


@pytest.mark.parametrize('step', [14, 15])
def test_final_abstract_checks_ignore_comments_but_reject_real_placeholders(tmp_path, step):
    for name in ('abstract_draft.md', 'citation_audit.md', 'derobotification.md'):
        (tmp_path / name).write_text('reviewed\n' * 21)
    paper = tmp_path / f'{tmp_path.name}_paper.tex'
    paper.write_text('% ABSTRACT_PLACEHOLDER: historical marker\n\\begin{abstract}Final abstract\\end{abstract}\n')
    validator = NativeArtifactValidator(Path(__file__).resolve().parents[1], step)
    check = getattr(validator, f'_step_{step}')
    assert check(tmp_path)[0]
    paper.write_text('\\begin{abstract}\\AbstractPlaceholder\\end{abstract}\n')
    assert not check(tmp_path)[0]
