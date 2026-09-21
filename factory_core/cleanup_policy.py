"""Single source of truth for the fail-closed project cleanup policy.

``scripts/cleanup_project_artifacts.py`` executes the policy and
``factory_core.steps.specialized.DeliveryStep`` records its fingerprint in the
``FINAL_SNAPSHOT_CREATED`` event, so the policy lives in the core package rather
than in the script.  Keeping one definition means cleanup and delivery can never
disagree about what is rebuildable, and changing the policy changes a fingerprint
that is written into delivery evidence.

The policy is fail-closed:

* only paths under :data:`REBUILDABLE_DIRS` or a declared temporary directory are
  ever *candidates* for removal;
* contract artifacts, machine evidence, canonical results and receipt references
  are *protected*;
* anything unrecognised is kept.
"""

from __future__ import annotations

import hashlib
import json
import re

# Only the *intermediate* trees are rebuildable.  ``final``/``unified`` trees are
# canonical delivery inputs (the ownership contract marks ``data/final/**`` as
# ``final_input=True``) and must never be removed wholesale.
REBUILDABLE_DIRS = frozenset(
    {
        ("data", "intermediate"),
        ("analysis", "intermediate"),
        ("replication", "intermediate"),
    }
)

TEMP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".ruff_cache",
        "tmp",
        "temp",
        "cache",
        ".cache",
    }
)

TEMP_DIR_PREFIXES = ("tmp_", "temp_", ".tmp")

TEMP_FILE_RE = re.compile(r"(^|[_-])tmp([_.-]|$)|\.(lock|pyc|pyo|swp)$|~$")

# Top-level trees that carry authority or evidence.  Nothing below them is a
# deletion candidate even if its name looks temporary.
PROTECTED_TOP_LEVEL = frozenset(
    {
        ".factory",
        ".git",
        "canonical",
        "evidence",
        "judge_outputs",
        "receipts",
        "selection",
        "submission",
    }
)

# Named contract and evidence artifacts.  Kept by name so a canonical result
# parked inside an intermediate tree cannot be collected as scratch data.
PROTECTED_NAMES = frozenset(
    {
        "canonical_results.json",
        "checkpoint.md",
        "chosen_method.md",
        "issue_ledger.json",
        "judge_evaluation.md",
        "method_decision.md",
        "model_contract.json",
        "paper.tex",
        "problem_contract.json",
        "provenance_verification.latest.txt",
        "solve_log.md",
        "status.json",
        "verification_summary.md",
    }
)

CLEANUP_POLICY_SCHEMA = "factory-cleanup-policy-v1"


def policy_fingerprint() -> str:
    """Stable hash of the deletion policy, for recording in delivery evidence."""

    payload = json.dumps(
        {
            "schema": CLEANUP_POLICY_SCHEMA,
            "rebuildable_dirs": sorted(sorted(item) for item in REBUILDABLE_DIRS),
            "protected_names": sorted(PROTECTED_NAMES),
            "protected_top_level": sorted(PROTECTED_TOP_LEVEL),
            "temp_dir_names": sorted(TEMP_DIR_NAMES),
            "temp_dir_prefixes": sorted(TEMP_DIR_PREFIXES),
            "temp_file_re": TEMP_FILE_RE.pattern,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
