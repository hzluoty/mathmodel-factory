"""The current ownership matcher must stay byte-for-byte equivalent to the
frozen v1 trust root while staying fast enough for project scans.

The current module precomputes globstar variants and normalizes each path once;
this regression binds it to the frozen reference over a corpus so the
optimization can never drift from the contract.
"""

from factory_core.artifact_ownership import (
    ARTIFACT_OWNERSHIP_REGISTRY as FROZEN_REGISTRY,
    artifact_pattern_matches,
)
from factory_core.current_artifact_ownership import (
    ADDITIONAL_OWNERSHIP,
    artifact_ownership,
)


FROZEN_ORDER = ADDITIONAL_OWNERSHIP + FROZEN_REGISTRY


def _reference(path):
    return next((rule for rule in FROZEN_ORDER if artifact_pattern_matches(rule.pattern, path)), None)


def _corpus():
    paths = []
    for rule in FROZEN_ORDER:
        paths.extend((
            rule.pattern,
            rule.pattern.replace("/**/", "/"),
            rule.pattern.replace("**/", ""),
        ))
    paths.extend((
        "references.bib", "./references.bib", "REFERENCES.BIB", "TABLES/X.TEX",
        "tables/x.tex", "tables\\x.tex", "tables/a/b.png", "figures/f1.pdf",
        "models/A-S-01/code/common.py", "judge_outputs/aggregate.json",
        "data/raw/p.txt", "problem/statement.md", "style/refs.bib",
        ".factory/solver_inputs/x.json", "STEP5_RECEIPT.json",
        "archive/old.md", "nested/problem/x.md", "a/**/b.md",
        "no_match_file.xyz", "",
    ))
    return paths


def test_current_matcher_matches_frozen_reference():
    mismatches = []
    for path in _corpus():
        current = artifact_ownership(path)
        reference = _reference(path)
        current_key = None if current is None else (current.pattern, current.owner_stage, current.dirty_flag)
        reference_key = None if reference is None else (reference.pattern, reference.owner_stage, reference.dirty_flag)
        if current_key != reference_key:
            mismatches.append((path, current_key, reference_key))
    assert mismatches == []


def test_current_matcher_is_cached_and_single_pass(tmp_path):
    artifact_ownership.cache_clear()
    path = "models/A-S-01/code/common.py"
    assert artifact_ownership(path) is not None
    assert artifact_ownership.cache_info().currsize == 1
    assert artifact_ownership(path) is artifact_ownership(path)
    assert artifact_ownership.cache_info().hits >= 1
