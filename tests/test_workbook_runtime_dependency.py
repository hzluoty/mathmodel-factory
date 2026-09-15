import builtins

import pytest

from scripts.verify_numbers import scan_results_directory


def test_result_workbook_is_verified_in_runtime(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active['A1'] = 7391
    workbook.save(tmp_path / 'result_probe.xlsx')
    workbook.close()
    assert 'result_probe.xlsx' in scan_results_directory(tmp_path)


def test_missing_workbook_dependency_is_an_error_not_an_empty_success(tmp_path, monkeypatch):
    (tmp_path / 'result_probe.xlsx').write_bytes(b'workbook must not be silently ignored')
    original_import = builtins.__import__

    def import_without_openpyxl(name, *args, **kwargs):
        if name == 'openpyxl':
            raise ImportError('simulated production installation without workbook support')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', import_without_openpyxl)
    with pytest.raises(RuntimeError, match='openpyxl is required'):
        scan_results_directory(tmp_path)


def test_json_only_project_does_not_require_workbook_import(tmp_path, monkeypatch):
    (tmp_path / 'results').mkdir()
    (tmp_path / 'results/values.json').write_text('{"score":7391}')
    original_import = builtins.__import__

    def import_without_openpyxl(name, *args, **kwargs):
        if name == 'openpyxl':
            raise ImportError('unavailable')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', import_without_openpyxl)
    assert scan_results_directory(tmp_path)['results/values.json']['score']['value'] == 7391
