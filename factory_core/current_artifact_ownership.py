"""Current native ownership, additive to the frozen M0.2/M0.3 v1 table.

The v1 module is a persisted compatibility trust root: do not edit its bytes
or indices when registering newly supported native artifacts. Current input
manifests explicitly carry this module's new schema, not the frozen identity.
"""
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from .artifact_ownership import (
    ARTIFACT_OWNERSHIP_REGISTRY as FROZEN_REGISTRY,
    ArtifactOwnership,
    artifact_pattern_matches,
    artifact_pattern_variants,
    normalize_artifact_path,
)

ARTIFACT_OWNERSHIP_SCHEMA = "factory-native-artifact-ownership-v3"
ADDITIONAL_OWNERSHIP = (
    ArtifactOwnership("models/reporting_scope/scope_review_manifest.json", 10, "reporting_scope_review", "FORMAT_DIRTY"),
    ArtifactOwnership("judge_evidence.json", 10, "final_audit_evidence", "FORMAT_DIRTY"),
    # Receipt-validated coverage includes exact initial versions individually.
    ArtifactOwnership(
        ".factory/solver_inputs/**", 4, "solver_input_snapshot", "RESULT_DIRTY",
        final_input=False, submission_member=False,
    ),
    ArtifactOwnership("method_fit_suggestions.json", 1, "method_fit_reference", "MODEL_DIRTY"),
    ArtifactOwnership("STEP5_RECEIPT.json", 4, "solver_evidence_projection", "RESULT_DIRTY"),
)
ARTIFACT_OWNERSHIP_REGISTRY = FROZEN_REGISTRY + ADDITIONAL_OWNERSHIP


# Frozen order is part of the contract: additional native rules win over the
# frozen table, exactly as before.
_OWNERSHIP_ORDER = ADDITIONAL_OWNERSHIP + FROZEN_REGISTRY


@lru_cache(maxsize=None)
def _pattern_variants(pattern):
    """Memoize the frozen globstar variant expansion (pure function of pattern)."""

    return artifact_pattern_variants(pattern)


@lru_cache(maxsize=65536)
def artifact_ownership(path):
    """Return the owning rule for one artifact path.

    Same semantics as the frozen matcher (case-insensitive, normalized globstar
    matching in registry order), but the path is normalized once per lookup and
    the per-pattern variant lists are precomputed. The previous formulation
    re-normalized the path and rebuilt the variants for every one of the ~88
    rules on every call, which dominated project scans (hundreds of thousands of
    matches per request) on large projects.
    """

    normalized = normalize_artifact_path(path).lower()
    for rule in _OWNERSHIP_ORDER:
        if any(fnmatchcase(normalized, candidate)
               for candidate in _pattern_variants(rule.pattern)):
            return rule
    return None


def artifact_owner_stage(path, *, default=None):
    owner = artifact_ownership(path)
    return owner.owner_stage if owner is not None else default


def reopen_after_step_for_artifact(path, *, default_stage=3):
    from .stages import resume_after_step_for_stage
    return resume_after_step_for_stage(artifact_owner_stage(path, default=default_stage))


def iter_owned_artifacts(project_dir, *, final_input_only=False,
                         submission_only=False, include_symlinks=False):
    project = Path(project_dir).resolve()
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        owner = artifact_ownership(relative.as_posix())
        if owner is None or (final_input_only and not owner.final_input):
            continue
        if submission_only and not owner.submission_member:
            continue
        if any(part in {"archive", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            if include_symlinks:
                yield path
        elif path.is_file():
            yield path
