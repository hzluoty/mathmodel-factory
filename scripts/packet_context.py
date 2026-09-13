"""Verify immutable packet context and role-local assets for all execution paths.

This module has no dependency on audit execution or later-phase orchestration.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HEADER_RE = re.compile(r"\n----- FILE: ([^\n]+) -----\n")
OMITTED_MARKER = "\n----- SOME SELECTED FILES OMITTED; SEE PACKET MANIFEST -----\n"


class GroundingError(ValueError):
    """A packet or role envelope cannot be verified."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _active_chunks(
    files: list[Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_path: dict[str, dict[str, Any]] = {}
    by_chunk_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise GroundingError(
                "MANIFEST_FILE_INVALID", f"manifest files[{index}] must be an object"
            )
        if item.get("status") not in {"included", "truncated"}:
            continue

        path = item.get("path")
        if (
            not isinstance(path, str)
            or not path.strip()
            or "\r" in path
            or "\n" in path
        ):
            raise GroundingError(
                "ACTIVE_CHUNK_PATH_INVALID",
                f"active manifest path is invalid at files[{index}]",
            )
        chunk_id = item.get("chunk_id")
        if not isinstance(chunk_id, str) or SHA256_RE.fullmatch(chunk_id) is None:
            raise GroundingError(
                "ACTIVE_CHUNK_ID_INVALID",
                f"active chunk_id is invalid for path: {path}",
            )
        included_sha256 = item.get("included_sha256")
        if (
            not isinstance(included_sha256, str)
            or SHA256_RE.fullmatch(included_sha256) is None
        ):
            raise GroundingError(
                "ACTIVE_CHUNK_HASH_INVALID",
                f"active included_sha256 is invalid for path: {path}",
            )
        included_bytes = item.get("included_bytes")
        if (
            not isinstance(included_bytes, int)
            or isinstance(included_bytes, bool)
            or included_bytes < 0
        ):
            raise GroundingError(
                "ACTIVE_CHUNK_BYTES_INVALID",
                f"active included_bytes is invalid for path: {path}",
            )
        source_line_start = item.get("source_line_start")
        if (
            not isinstance(source_line_start, int)
            or isinstance(source_line_start, bool)
            or source_line_start < 1
        ):
            raise GroundingError(
                "ACTIVE_CHUNK_SOURCE_LINE_INVALID",
                f"active source_line_start is invalid for path: {path}",
            )
        if path in by_path:
            raise GroundingError(
                "DUPLICATE_ACTIVE_PATH", f"duplicate active manifest path: {path}"
            )
        if chunk_id in by_chunk_id:
            raise GroundingError(
                "DUPLICATE_ACTIVE_CHUNK_ID",
                f"duplicate active manifest chunk_id: {chunk_id}",
            )
        by_path[path] = item
        by_chunk_id[chunk_id] = item
    return by_path, by_chunk_id


def _read_role_asset(manifest_dir: Path | None, relative: str) -> bytes:
    if manifest_dir is None:
        raise ValueError("role-local asset loader is required")
    directory = manifest_dir / "assets"
    asset = manifest_dir / relative
    if directory.is_symlink() or asset.is_symlink() or not asset.is_file():
        raise ValueError("role asset is missing or traverses a symlink")
    asset.resolve(strict=True).relative_to(manifest_dir.resolve())
    return asset.read_bytes()


def _context_sections(
    text: str, expected_by_path: dict[str, dict[str, Any]] | list[dict[str, Any]],
    manifest_dir: Path | None = None, *, asset_loader=None
) -> dict[str, dict[str, Any]]:
    if isinstance(expected_by_path, list):
        expected_by_path, _ = _active_chunks(expected_by_path)
    matches = list(HEADER_RE.finditer(text))
    sections: dict[str, dict[str, Any]] = {}
    for index, match in enumerate(matches):
        path = match.group(1)
        if path in sections:
            raise GroundingError(
                "DUPLICATE_CONTEXT_SECTION", f"duplicate context section: {path}"
            )
        item = expected_by_path.get(path)
        if item is None:
            raise GroundingError(
                "UNDECLARED_CONTEXT_SECTION", f"undeclared context section: {path}"
            )

        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[match.end() : end]
        if index + 1 == len(matches) and raw.endswith(OMITTED_MARKER):
            raw = raw[: -len(OMITTED_MARKER)]
        candidates = [raw]
        if raw.endswith("\n"):
            candidates.append(raw[:-1])
        if raw.endswith("\n\n"):
            candidates.append(raw[:-2])

        expected_hash = item["included_sha256"]
        hash_matches = [
            candidate
            for candidate in candidates
            if sha256_bytes(candidate.encode("utf-8")) == expected_hash
        ]
        if not hash_matches:
            raise GroundingError(
                "CONTEXT_SECTION_HASH_MISMATCH",
                f"context section hash does not match manifest: {path}",
            )
        expected_bytes = item["included_bytes"]
        content = next(
            (
                candidate
                for candidate in hash_matches
                if len(candidate.encode("utf-8")) == expected_bytes
            ),
            None,
        )
        if content is None:
            raise GroundingError(
                "CONTEXT_SECTION_SIZE_MISMATCH",
                f"context section byte count does not match manifest: {path}",
            )
        sections[path] = {
            "text": content,
            "context_line_start": text.count("\n", 0, match.end()) + 1,
        }
    for path, item in expected_by_path.items():
        if item.get("content_location") != "asset":
            continue
        relative = item.get("asset_path")
        if (not isinstance(relative, str) or len(relative.split("/")) != 2
                or not relative.startswith("assets/") or "\\" in relative
                or relative.split("/")[1] in {"", ".", ".."}):
            raise GroundingError("ASSET_PATH_INVALID", "asset must be directly inside its role assets directory")
        try:
            data = (asset_loader(relative) if asset_loader is not None
                    else _read_role_asset(manifest_dir, relative))
        except (OSError, KeyError, ValueError) as exc:
            raise GroundingError("ASSET_UNREADABLE", "role asset is missing or unsafe") from exc
        if len(data) != item.get("asset_size") or sha256_bytes(data) != item.get("asset_sha256"):
            raise GroundingError("ASSET_INVALID", f"role asset hash mismatch: {path}")
        mode = item.get("asset_quote_mode")
        if mode == "text":
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GroundingError("ASSET_INVALID", "text asset is not UTF-8") from exc
            if (sha256_bytes(data) != item.get("included_sha256")
                    or len(data) != item.get("included_bytes")):
                raise GroundingError("ASSET_INVALID", f"text asset chunk hash mismatch: {path}")
            sections[path] = {"text": content, "context_line_start": None, "asset_path": relative}
        elif mode != "descriptor":
            raise GroundingError("ASSET_INVALID", "unsupported asset quote mode")
    missing = sorted(set(expected_by_path) - set(sections))
    if missing:
        raise GroundingError(
            "MISSING_CONTEXT_SECTION",
            "context omits manifest chunks: " + ", ".join(missing),
        )
    return sections
