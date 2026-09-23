import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from web.backend.upload_service import (
    ArchiveBudgetError,
    ArchiveTraversalError,
    ExtractionLimits,
    extract_archive,
    find_problem_file,
)
from web.backend.project_api import _safe_upload_filename


def test_extract_archive_rejects_zip_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.md", "boom")

    with pytest.raises(ArchiveTraversalError):
        extract_archive(archive, tmp_path / "out")


def test_extract_archive_rejects_tar_traversal(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("../escape.pdf")
        data = b"boom"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    with pytest.raises(ArchiveTraversalError):
        extract_archive(archive, tmp_path / "out")


def test_find_problem_file_prefers_problem_named_pdf(tmp_path):
    (tmp_path / "附件").mkdir()
    (tmp_path / "题目_problem.pdf").write_text("pdf", encoding="utf-8")
    (tmp_path / "附件" / "notes.md").write_text("md", encoding="utf-8")

    found = find_problem_file(tmp_path)

    assert found.name == "题目_problem.pdf"


def test_upload_filename_is_reduced_to_safe_basename():
    assert _safe_upload_filename("../escape.pdf") == "escape.pdf"
    assert _safe_upload_filename("nested/../../problem.md") == "problem.md"


def test_upload_filename_rejects_empty_basename():
    with pytest.raises(ValueError):
        _safe_upload_filename("../")


def test_extract_archive_enforces_uncompressed_budget(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"\0" * (256 * 1024))

    out = tmp_path / "out"
    with pytest.raises(ArchiveBudgetError):
        extract_archive(archive, out, limits=ExtractionLimits(max_total_bytes=4096))

    assert not any(path.is_file() for path in out.rglob("*"))


def test_extract_archive_enforces_member_count(tmp_path):
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for index in range(5):
            zf.writestr(f"f{index}.md", "x")

    with pytest.raises(ArchiveBudgetError, match="members"):
        extract_archive(
            archive, tmp_path / "out", limits=ExtractionLimits(max_members=3)
        )


def test_extract_archive_enforces_per_file_budget(tmp_path):
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("big.txt", b"x" * 2048)

    with pytest.raises(ArchiveBudgetError, match="limit is"):
        extract_archive(
            archive, tmp_path / "out", limits=ExtractionLimits(max_file_bytes=1024)
        )


def test_extract_archive_enforces_time_budget(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("problem.md", "hello")

    with pytest.raises(ArchiveBudgetError, match="time budget"):
        extract_archive(
            archive, tmp_path / "out", limits=ExtractionLimits(max_seconds=0)
        )


def test_extract_archive_accepts_archive_within_budget(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("problem.md", "hello")
        zf.writestr("data/notes.txt", "notes")

    out = tmp_path / "out"
    extract_archive(archive, out)

    assert (out / "problem.md").read_text(encoding="utf-8") == "hello"
    assert find_problem_file(out).name == "problem.md"


def test_extract_archive_rejects_tar_links(tmp_path):
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("link.md")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)

    with pytest.raises(ArchiveTraversalError, match="links not allowed"):
        extract_archive(archive, tmp_path / "out")


def test_extract_archive_discards_partial_output_on_failure(tmp_path, monkeypatch):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.md", "one")
        zf.writestr("second.md", "two")

    # Patch the namespace ``extract_archive`` actually resolves ``_copy_bounded``
    # from: other test modules reload web.backend.upload_service, so patching the
    # attribute on a freshly imported module object is not reliable here.
    namespace = extract_archive.__globals__
    original = namespace["_copy_bounded"]
    calls = {"n": 0}

    def flaky(source, sink, *, allowance, deadline):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ArchiveBudgetError("simulated mid-extraction failure")
        return original(source, sink, allowance=allowance, deadline=deadline)

    monkeypatch.setitem(namespace, "_copy_bounded", flaky)
    out = tmp_path / "out"
    with pytest.raises(ArchiveBudgetError):
        extract_archive(archive, out)

    assert not any(path.is_file() for path in out.rglob("*")), (
        "a rejected archive must not leave a partial tree behind"
    )
