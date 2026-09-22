"""Tests for the bounded log-tail reader.

The boundedness test spies on the bytes actually read, because correctness alone
would pass just as well with the old read-everything implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from web.backend.log_tail import read_tail_lines


class _CountingHandle:
    def __init__(self, handle, owner: "_CountingPath") -> None:
        self._handle = handle
        self._owner = owner

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._handle.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        self._owner.bytes_read += len(data)
        return data

    def close(self) -> None:
        self._handle.close()


class _CountingPath:
    """Path-like object that records how many bytes were read through it."""

    def __init__(self, real: Path) -> None:
        self._real = real
        self.bytes_read = 0

    def stat(self):
        return self._real.stat()

    def open(self, mode: str = "rb"):
        return _CountingHandle(self._real.open(mode), self)


def _write_lines(path: Path, lines: list[str], trailing_newline: bool = True) -> str:
    text = "\n".join(lines) + ("\n" if trailing_newline else "")
    path.write_text(text, encoding="utf-8")
    return text


def test_returns_the_last_lines_of_a_small_file(tmp_path: Path) -> None:
    path = tmp_path / "small.log"
    _write_lines(path, ["one", "two", "three", "four"])
    assert read_tail_lines(path, 2) == ["three", "four"]
    assert read_tail_lines(path, 99) == ["one", "two", "three", "four"]


def test_matches_a_full_read_for_a_large_file(tmp_path: Path) -> None:
    path = tmp_path / "many.log"
    text = _write_lines(path, [f"line-{index:06d}" for index in range(50_000)])
    expected = text.splitlines()
    for count in (1, 7, 200, 5_000):
        assert read_tail_lines(path, count) == expected[-count:]


def test_read_is_bounded_by_the_requested_tail(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    text = _write_lines(path, [f"line-{index:06d}" for index in range(300_000)])
    size = path.stat().st_size

    counting = _CountingPath(path)
    tail = read_tail_lines(counting, 20)

    assert tail == text.splitlines()[-20:]
    # The old implementation read `size` bytes here; a tail of 20 short lines
    # needs a fraction of one block.
    assert counting.bytes_read < size // 10
    assert counting.bytes_read <= 2 * 64 * 1024


def test_handles_a_file_without_a_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "no-newline.log"
    _write_lines(path, ["alpha", "beta", "gamma"], trailing_newline=False)
    assert read_tail_lines(path, 2) == ["beta", "gamma"]
    assert read_tail_lines(path, 10) == ["alpha", "beta", "gamma"]


def test_a_file_with_no_newlines_at_all(tmp_path: Path) -> None:
    path = tmp_path / "one-line.log"
    path.write_text("single line, no newline", encoding="utf-8")
    assert read_tail_lines(path, 5) == ["single line, no newline"]


def test_multibyte_lines_survive_block_boundaries(tmp_path: Path) -> None:
    """A 7-byte block guarantees boundaries inside multi-byte characters."""

    path = tmp_path / "cjk.log"
    lines = [f"第{index}步：建模完成，无异常" for index in range(300)]
    _write_lines(path, lines)
    assert read_tail_lines(path, 5, block_bytes=7) == lines[-5:]


def test_missing_and_empty_files_yield_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "gone.log"
    assert read_tail_lines(missing, 10) == []
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert read_tail_lines(empty, 10) == []


def test_line_count_is_clamped_to_at_least_one(tmp_path: Path) -> None:
    path = tmp_path / "clamp.log"
    _write_lines(path, ["first", "second"])
    assert read_tail_lines(path, 0) == ["second"]
    assert read_tail_lines(path, -5) == ["second"]


def test_max_bytes_bounds_the_read_and_drops_the_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "wide.log"
    body = "".join(f"{index:08d}-{'x' * 200}\n" for index in range(500))
    path.write_text(body, encoding="utf-8")

    counting = _CountingPath(path)
    tail = read_tail_lines(counting, 100, max_bytes=1024)

    assert counting.bytes_read <= 1024, "the cap must bound the read itself"
    assert 0 < len(tail) < 100, "a capped window cannot satisfy the request"
    # The window began mid-file, so its partial first line is dropped and every
    # returned line is whole.
    assert all(len(line) == 209 for line in tail)


def test_a_single_line_longer_than_the_cap_still_returns_something(tmp_path: Path) -> None:
    path = tmp_path / "huge-line.log"
    path.write_text("y" * 5_000 + "\n", encoding="utf-8")
    tail = read_tail_lines(path, 5, max_bytes=512)
    # Bounded reads cannot reconstruct a line that does not fit in the window;
    # returning the truncated remainder is the honest answer.
    assert len(tail) == 1
    assert len(tail[0]) <= 512


def test_stat_failure_does_not_raise(tmp_path: Path) -> None:
    class _Broken:
        def stat(self):
            raise OSError("permission denied")

    assert read_tail_lines(_Broken(), 5) == []


def test_a_directory_is_not_mistaken_for_content(tmp_path: Path) -> None:
    # stat() succeeds for a directory and size is non-zero on most filesystems,
    # so the open() failure path is what has to absorb this.
    assert read_tail_lines(tmp_path, 5) == []


@pytest.mark.parametrize("block_bytes", [1, 2, 3, 1024])
def test_tiny_blocks_still_produce_intact_lines(tmp_path: Path, block_bytes: int) -> None:
    path = tmp_path / "tiny.log"
    lines = [f"row-{index}" for index in range(120)]
    _write_lines(path, lines)
    assert read_tail_lines(path, 8, block_bytes=block_bytes) == lines[-8:]
