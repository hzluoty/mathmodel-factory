"""Safe archive extraction for uploaded problem packages.

Name checks alone are not a resource bound: a small, highly compressible archive
can expand to an unbounded amount of disk.  Extraction therefore also enforces

* a total uncompressed byte budget,
* a per-file byte budget,
* a member-count budget, and
* a wall-clock budget,

and streams every member through a bounded copy instead of calling
``extractall``.  Declared sizes are checked first for a cheap rejection, but the
streaming copy is what actually enforces the budget, because a declared size in
a hostile archive cannot be trusted.

On failure the members created so far are removed, so a rejected archive does
not leave a partial tree behind.
"""

from __future__ import annotations

import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

_COPY_CHUNK = 1 << 20


class ArchiveExtractionError(RuntimeError):
    """Base class for archives this module refuses to extract."""


class ArchiveTraversalError(ArchiveExtractionError):
    """A member name or link would escape the extraction root."""


class ArchiveBudgetError(ArchiveExtractionError):
    """The archive exceeds a declared extraction budget."""


@dataclass(frozen=True)
class ExtractionLimits:
    max_total_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 256 * 1024 * 1024
    max_members: int = 10_000
    max_seconds: float = 120.0


DEFAULT_LIMITS = ExtractionLimits()


def _safe_target(root: Path, name: str) -> Path:
    resolved_root = root.resolve()
    target = (resolved_root / name).resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ArchiveTraversalError(f"archive member escapes root: {name}")
    return target


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise ArchiveBudgetError("archive extraction exceeded the time budget")


def _copy_bounded(source, sink, *, allowance: int, deadline: float) -> int:
    """Copy at most ``allowance`` bytes; raise once the real total exceeds it."""

    written = 0
    while True:
        _check_deadline(deadline)
        chunk = source.read(_COPY_CHUNK)
        if not chunk:
            return written
        written += len(chunk)
        if written > allowance:
            raise ArchiveBudgetError(
                "archive exceeds the uncompressed size budget while extracting"
            )
        sink.write(chunk)


def _discard(created: list[Path]) -> None:
    for path in reversed(created):
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            continue


def _allowance(limits: ExtractionLimits, extracted: int) -> int:
    remaining = limits.max_total_bytes - extracted
    if remaining <= 0:
        raise ArchiveBudgetError(
            f"archive expands beyond the {limits.max_total_bytes} byte budget"
        )
    return min(limits.max_file_bytes, remaining)


def _extract_zip(archive_path, out_dir, limits, deadline, created):
    with zipfile.ZipFile(archive_path) as zf:
        members = zf.infolist()
        if len(members) > limits.max_members:
            raise ArchiveBudgetError(
                f"archive has {len(members)} members, limit is {limits.max_members}"
            )
        declared = 0
        for member in members:
            _safe_target(out_dir, member.filename)
            if member.is_dir():
                continue
            if member.file_size > limits.max_file_bytes:
                raise ArchiveBudgetError(
                    f"archive member {member.filename} declares "
                    f"{member.file_size} bytes, limit is {limits.max_file_bytes}"
                )
            declared += member.file_size
            if declared > limits.max_total_bytes:
                raise ArchiveBudgetError(
                    f"archive declares more than {limits.max_total_bytes} "
                    "bytes uncompressed"
                )

        extracted = 0
        for member in members:
            _check_deadline(deadline)
            target = _safe_target(out_dir, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                created.append(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            created.append(target)
            allowance = _allowance(limits, extracted)
            with zf.open(member) as source, target.open("wb") as sink:
                extracted += _copy_bounded(
                    source, sink, allowance=allowance, deadline=deadline
                )


def _extract_tar(archive_path, out_dir, limits, deadline, created):
    with tarfile.open(archive_path, "r:*") as tf:
        members = tf.getmembers()
        if len(members) > limits.max_members:
            raise ArchiveBudgetError(
                f"archive has {len(members)} members, limit is {limits.max_members}"
            )
        for member in members:
            _safe_target(out_dir, member.name)
            if member.issym() or member.islnk():
                raise ArchiveTraversalError(f"links not allowed: {member.name}")

        extracted = 0
        for member in members:
            _check_deadline(deadline)
            target = _safe_target(out_dir, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                created.append(target)
                continue
            if not member.isfile():
                continue
            if member.size > limits.max_file_bytes:
                raise ArchiveBudgetError(
                    f"archive member {member.name} declares {member.size} bytes, "
                    f"limit is {limits.max_file_bytes}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            created.append(target)
            allowance = _allowance(limits, extracted)
            source = tf.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as sink:
                extracted += _copy_bounded(
                    source, sink, allowance=allowance, deadline=deadline
                )


def extract_archive(
    archive_path: Path,
    out_dir: Path,
    *,
    limits: ExtractionLimits | None = None,
) -> None:
    resolved = limits or DEFAULT_LIMITS
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + resolved.max_seconds
    created: list[Path] = []
    try:
        if zipfile.is_zipfile(archive_path):
            _extract_zip(archive_path, out_dir, resolved, deadline, created)
            return
        _extract_tar(archive_path, out_dir, resolved, deadline, created)
    except BaseException:
        _discard(created)
        raise


def find_problem_file(root: Path) -> Path | None:
    preferred: list[Path] = []
    fallback: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".pdf", ".md"}:
            continue
        fallback.append(path)
        lowered = path.name.lower()
        if any(key in lowered for key in ("problem", "question", "题目", "题")):
            preferred.append(path)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return None
