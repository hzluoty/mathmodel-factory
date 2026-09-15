import json
import random

import pytest

from scripts.number_manifest_stream import iter_manifest_records, verify_sources
from scripts.verify_numbers import compute_checksum, scan_results_directory, iter_result_numbers, verify_paper, _extract_numbers_from_lines


def entry(value):
    return {"value": value, "checksum": compute_checksum(value), "type": type(value).__name__}


def write_manifest(tmp_path, sources):
    path = tmp_path / "numbers_manifest.json"
    path.write_text(json.dumps({"generated_by": "测试", "step": "Step 10", "sources": sources}, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 13, 65536])
def test_json_boundaries(tmp_path, chunk):
    sources = {"a\\\"汉.xlsx": {"Sheet 1!A1": entry(-1.25e-10), "abc": entry(12345)}, "empty": {}}
    path = write_manifest(tmp_path, sources)
    assert list(iter_manifest_records(path, chunk)) == [(s, k, e) for s, values in sources.items() for k, e in values.items()]


@pytest.mark.parametrize("content", ['{}', '{"sources":{}', '{"sources":{}} extra', '{"sources":{"a":{"x":{}}}}'])
def test_invalid_manifest_fails_closed(tmp_path, content):
    path = tmp_path / "numbers_manifest.json"
    path.write_text(content)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        list(iter_manifest_records(path, 3))


def test_boolean_cannot_reuse_an_integer_manifest_checksum(tmp_path):
    path = write_manifest(tmp_path, {'a.json': {'x': {**entry(1), 'value': True}}})
    with pytest.raises(ValueError, match='Invalid manifest entry'):
        list(iter_manifest_records(path))


def test_all_source_keys_checked_despite_equal_numeric_values(tmp_path):
    sources = {"a.json": {"int": entry(1), "float": entry(1.0), "gone": entry(2)}, "deleted.json": {"x": entry(3)}}
    write_manifest(tmp_path, sources)
    current = [("a.json", "float", entry(1)), ("a.json", "int", entry(1)), ("new.json", "new", entry(99))]
    errors, untraced = verify_sources(tmp_path, [(1, "", 1.0), (2, "", 99)], iter(current))
    assert list(errors) == [("a.json", "float", "checksum"), ("a.json", "gone", "missing"), ("deleted.json", "x", "missing")]
    assert untraced == [(2, "", 99)]
    errors.close()


def test_matching_equals_legacy_oracle(tmp_path):
    rng = random.Random(492)
    values = [rng.uniform(-1000, 1000) for _ in range(300)] + [0, 1e-8, -1e-8]
    paper = [v + sign * max(1e-6, abs(v)*.005) * factor for v in values for sign in [-1, 1] for factor in [.999999999, 1, 1.000000001]] + [9999]
    records = [("a", str(i), entry(v)) for i, v in enumerate(values)]
    write_manifest(tmp_path, {"a": {key: e for _, key, e in records}})
    numbers = [(i, "", v) for i, v in enumerate(paper)]
    errors, actual = verify_sources(tmp_path, numbers, reversed(records))
    expected = [item for item in numbers if not any(abs(v-item[2]) <= max(1e-6, abs(v)*.005) for v in values)]
    assert not errors
    assert actual == expected
    errors.close()


def test_json_xlsx_coverage_and_stale_cell(tmp_path):
    from openpyxl import Workbook
    (tmp_path / "results").mkdir()
    (tmp_path / "results/a.json").write_text('{"x":[1,2.5,true],"z":{"k":-30}}')
    wb = Workbook()
    wb.active.append([1, 1.0, 31.25, True, "text"])
    wb.save(tmp_path / "result1.xlsx")
    sources = scan_results_directory(tmp_path)
    assert len(list(iter_result_numbers(tmp_path))) == 6
    write_manifest(tmp_path, sources)
    (tmp_path / "demo_paper.tex").write_text('\\begin{document}\n31.25\n\\end{document}')
    assert verify_paper(tmp_path, "demo")
    wb.active["C1"] = 32.25
    wb.save(tmp_path / "result1.xlsx")
    assert not verify_paper(tmp_path, "demo")
    assert 'result1.xlsx::Sheet!C1' in (tmp_path / "number_verification.md").read_text()


def test_tex_ranges_keep_endpoints_and_negative_measurements():
    numbers = _extract_numbers_from_lines([(1, '', '0--72 h; 30---80; -50 degrees; -0.00951 seconds')], assume_document=True)
    assert [value for _, _, value in numbers] == [0, 72, 30, 80, -50, -0.00951]


def test_section_references_do_not_hide_measurements():
    numbers = _extract_numbers_from_lines([(1, '', '第3.2、7节，第3.2节，第1章。温度3.2，比例7.4%，网格6.25')], assume_document=True)
    assert [value for _, _, value in numbers] == [3.2, 7.4, 6.25]
