"""Exact project-specific routes for reviewed evidence outside standard folders.

Routes include files; they cannot exclude an artifact or grant quality approval.
Each declaration is bound to exact bytes and retained in the final submission.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .solver_input_versions import _identity, _write_once
from .solver_input_coverage import _canonical_hash, _regular_project_file

ROUTE_ROOT = ".factory/finalization/submission_routes"
ROUTE_SCHEMA = "factory-explicit-submission-route-v1"


@dataclass(frozen=True)
class SubmissionRoutes:
    files: tuple[Path, ...]
    evidence: tuple[Path, ...]
    roles: dict[str, dict[str, Any]]


def register_submission_route(
    project_dir: str | Path, *, relative_path: str, expected_sha256: str,
    owner_stage: int, semantic_domain: str, review_path: str, reason: str,
) -> Path:
    project = Path(project_dir).resolve()
    path, record = _identity(project, relative_path)
    if record['sha256'] != expected_sha256:
        raise ValueError('submission route reviewed file hash mismatch')
    if type(owner_stage) is not int or owner_stage not in range(1, 11) or not semantic_domain.strip() or not reason.strip():
        raise ValueError('submission route requires an owner, domain and reason')
    review, review_record = _identity(project, review_path)
    review_bytes = review.read_bytes()
    if not review_bytes or hashlib.sha256(review_bytes).hexdigest() != review_record['sha256']:
        raise ValueError('submission route review is empty or changed')
    review_relative = f"{ROUTE_ROOT}/reviews/{review_record['sha256']}.md"
    _write_once(project, review_relative, review_bytes)
    identity = {
        'schema_version': ROUTE_SCHEMA, 'file': record,
        'owner_stage': owner_stage, 'semantic_domain': semantic_domain.strip(),
        'reason': reason.strip(), 'scope': 'include_in_final_input_and_submission',
        'review': {**review_record, 'path': review_relative},
    }
    value = {**identity, 'content_sha256': _canonical_hash(identity)}
    key = _canonical_hash({'path': record['path'], 'sha256': expected_sha256})
    return _write_once(project, f'{ROUTE_ROOT}/bindings/{key}.json',
                       (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode())


def declared_submission_routes(project_dir: str | Path) -> SubmissionRoutes:
    project = Path(project_dir).resolve()
    root = project / ROUTE_ROOT / 'bindings'
    # Validate parent directories even if an attacker replaces the entire root.
    cursor = project
    for component in root.relative_to(project).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise ValueError('submission route directory is unsafe')
        if not cursor.exists():
            return SubmissionRoutes((), (), {})
        if not cursor.is_dir():
            raise ValueError('submission route directory is unsafe')
    candidates: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for p in sorted(root.glob('*.json')):
        p = _regular_project_file(project, p.relative_to(project).as_posix(), label='submission route binding')
        try:
            value = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('invalid submission route binding') from exc
        if not isinstance(value, dict) or not isinstance(value.get('file'), dict):
            raise ValueError('invalid submission route object')
        identity = {k: v for k, v in value.items() if k != 'content_sha256'}
        if value.get('content_sha256') != _canonical_hash(identity) or value.get('schema_version') != ROUTE_SCHEMA:
            raise ValueError('submission route binding hash or schema mismatch')
        if value.get('scope') != 'include_in_final_input_and_submission' or type(value.get('owner_stage')) is not int or value['owner_stage'] not in range(1, 11):
            raise ValueError('submission route scope or owner is invalid')
        if not str(value.get('semantic_domain') or '').strip() or not str(value.get('reason') or '').strip():
            raise ValueError('submission route domain or reason missing')
        record = value['file']
        relative = str(record.get('path') or '')
        key = _canonical_hash({'path': relative, 'sha256': record.get('sha256')})
        if p.name != f'{key}.json':
            raise ValueError('submission route binding filename mismatch')
        candidates.setdefault(relative, []).append((p, value))
    files, evidence, roles = {}, {}, {}
    for relative, values in candidates.items():
        path, current = _identity(project, relative)
        matching = [(p, v) for p, v in values if v['file'] == current]
        if len(matching) != 1:
            raise ValueError(f'submission route current file drift: {relative}')
        binding, value = matching[0]
        review = value.get('review')
        if not isinstance(review, dict) or not str(review.get('path', '')).startswith(f'{ROUTE_ROOT}/reviews/'):
            raise ValueError('submission route review path invalid')
        review_path, actual = _identity(project, review['path'])
        if actual != review or actual['size'] == 0:
            raise ValueError('submission route review identity mismatch')
        files[relative] = path
        evidence[binding.relative_to(project).as_posix()] = binding
        evidence[review_path.relative_to(project).as_posix()] = review_path
        roles[relative] = {'owner_stage': value['owner_stage'], 'semantic_domain': value['semantic_domain']}
    return SubmissionRoutes(tuple(files[k] for k in sorted(files)),
                            tuple(evidence[k] for k in sorted(evidence)), roles)
