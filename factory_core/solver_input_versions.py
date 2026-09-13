"""Bind a reviewed current input to exact, retained historical Solver bytes.

This is a provenance record, not a Solver-success or quality approval receipt.
Original submission receipts are never rewritten. Unregistered drift still fails.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

VERSION_SCHEMA = "factory-solver-input-version-v1"
VERSION_ROOT = ".factory/finalization/input_versions"


def _helpers():
    from .solver_input_coverage import _canonical_hash, _regular_project_file
    return _canonical_hash, _regular_project_file


def _identity(project: Path, relative: str) -> tuple[Path, dict[str, Any]]:
    _, regular = _helpers()
    path = regular(project, relative, label="solver input version evidence")
    before = path.stat()
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise ValueError("solver input version evidence changed while hashing")
    return path, {"path": path.relative_to(project).as_posix(),
                  "size": after.st_size, "sha256": digest}


def _record_relative(relative: str, historical_sha: str, current_sha: str) -> str:
    canonical, _ = _helpers()
    key = canonical({"path": relative, "input_sha256": historical_sha,
                     "current_sha256": current_sha})
    return f"{VERSION_ROOT}/bindings/{key}.json"


def _write_once(project: Path, relative: str, data: bytes) -> Path:
    # This helper only receives paths constructed below from fixed prefixes/hashes.
    path = project / relative
    cursor = project
    for component in path.relative_to(project).parts[:-1]:
        cursor = cursor / component
        if cursor.is_symlink():
            raise ValueError("solver input version directory traverses a symlink")
        cursor.mkdir(exist_ok=True)
        if not cursor.is_dir():
            raise ValueError("solver input version evidence directory is invalid")
    if path.is_symlink():
        raise ValueError("solver input version evidence must not be a symlink")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        if not path.is_file() or path.read_bytes() != data:
            raise ValueError("solver input version evidence is append-only")
        return path
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def register_solver_input_version(
    project_dir: str | Path, *, relative_path: str, historical_path: str,
    input_sha256: str, input_size: int, current_sha256: str,
    review_path: str, reason: str,
) -> Path:
    """Register only independently located exact historical and current bytes.

    Callers must review the change first. Both SHA values are required assertions,
    never inferred approval of whatever happens to occupy the current path.
    """
    project = Path(project_dir).resolve()
    canonical, _ = _helpers()
    current, current_record = _identity(project, relative_path)
    historical, historical_record = _identity(project, historical_path)
    review, review_record = _identity(project, review_path)
    relative = current.relative_to(project).as_posix()
    if current_record["sha256"] != current_sha256:
        raise ValueError("reviewed current solver input hash mismatch")
    if historical_record["sha256"] != input_sha256 or historical_record["size"] != input_size:
        raise ValueError("historical solver input does not match submission receipt")
    if not reason.strip() or review_record["size"] == 0:
        raise ValueError("solver input version requires nonempty review and reason")
    old_bytes = historical.read_bytes()
    review_bytes = review.read_bytes()
    if hashlib.sha256(old_bytes).hexdigest() != input_sha256 or hashlib.sha256(review_bytes).hexdigest() != review_record["sha256"]:
        raise ValueError("solver input version evidence changed while copying")
    archived_relative = f"{VERSION_ROOT}/blobs/{input_sha256}.bin"
    review_relative = f"{VERSION_ROOT}/reviews/{review_record['sha256']}.md"
    _write_once(project, archived_relative, old_bytes)
    _write_once(project, review_relative, review_bytes)
    identity = {
        "schema_version": VERSION_SCHEMA,
        "path": relative, "input_sha256": input_sha256, "input_size": input_size,
        "current": current_record,
        "historical": {"path": archived_relative, "sha256": input_sha256, "size": input_size},
        "review": {**review_record, "path": review_relative},
        "reason": reason.strip(),
        "scope": "historical_input_provenance_only",
        "solver_success_or_quality_approval": False,
    }
    record = {**identity, "content_sha256": canonical(identity)}
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return _write_once(project, _record_relative(relative, input_sha256, current_sha256), encoded)


def reviewed_solver_input_version(
    project: Path, input_record: dict[str, Any], current_sha256: str,
) -> tuple[Path, ...] | None:
    """Return validated historical evidence, or None for unregistered drift."""
    canonical, regular = _helpers()
    relative = str(input_record.get("path") or "")
    record_relative = _record_relative(relative, str(input_record.get("sha256") or ""), current_sha256)
    candidate = project / record_relative
    if not candidate.exists() and not candidate.is_symlink():
        return None
    path = regular(project, record_relative, label="solver input version binding")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid solver input version binding") from exc
    if not isinstance(record, dict):
        raise ValueError("solver input version binding must be an object")
    identity = {k: v for k, v in record.items() if k != "content_sha256"}
    if record.get("content_sha256") != canonical(identity):
        raise ValueError("solver input version binding content hash mismatch")
    expected = {"schema_version": VERSION_SCHEMA, "path": relative,
                "input_sha256": input_record.get("sha256"), "input_size": input_record.get("size"),
                "scope": "historical_input_provenance_only", "solver_success_or_quality_approval": False}
    if any(record.get(k) != v for k, v in expected.items()) or not str(record.get("reason") or "").strip():
        raise ValueError("solver input version binding does not match submission receipt")
    current, actual_current = _identity(project, relative)
    if actual_current != record.get("current") or actual_current["sha256"] != current_sha256:
        raise ValueError("reviewed current solver input identity drift")
    paths = [path]
    for key, prefix in (("historical", f"{VERSION_ROOT}/blobs/"), ("review", f"{VERSION_ROOT}/reviews/")):
        expected_record = record.get(key)
        if not isinstance(expected_record, dict) or not str(expected_record.get("path", "")).startswith(prefix):
            raise ValueError(f"solver input version {key} evidence route invalid")
        evidence_path, actual = _identity(project, expected_record["path"])
        if actual != expected_record or (key == "review" and actual["size"] <= 0):
            raise ValueError(f"solver input version {key} evidence identity mismatch")
        if key == "historical" and (actual["sha256"] != input_record["sha256"] or actual["size"] != input_record["size"]):
            raise ValueError("historical solver input does not match submission receipt")
        paths.append(evidence_path)
    return tuple(paths)
