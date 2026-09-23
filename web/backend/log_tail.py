"""Bounded reverse reading of a log file's tail.

The dashboard's log endpoint used to read an entire log and then slice its last
N lines.  Logs in this repository reach several megabytes, so a ``?lines=200``
request made the server read -- and immediately discard -- almost all of the
file, on the event loop.  This module reads backwards from the end instead, so
the cost tracks the tail actually requested.

Two details are load-bearing:

**The window is decoded once.**  Decoding each block as it is read would split
multi-byte characters that straddle a block boundary and silently corrupt log
text.  Blocks are gathered as bytes and decoded together at the end, so only a
truncated window's first line can carry a replacement character -- and that line
is dropped when another follows it.

**The read is capped.**  ``max_bytes`` bounds how far back a single request may
look, so a file with no newlines cannot turn one request into a full read of a
gigabyte.  When the cap is reached the caller gets the lines found in the
window, which is the honest answer for a bounded read.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_BLOCK_BYTES = 64 * 1024
DEFAULT_MAX_BYTES = 1024 * 1024


def read_tail_lines(
    path: Path,
    lines: int,
    *,
    block_bytes: int = DEFAULT_BLOCK_BYTES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[str]:
    """Return the last ``lines`` lines of ``path``, reading backwards.

    A missing or unreadable file yields an empty list rather than raising: the
    endpoint would otherwise turn a rotated-away log into a 500.
    """

    wanted = max(1, lines)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []

    window = bytearray()
    newlines = 0
    position = size
    handle = None
    try:
        handle = path.open("rb")
        while position > 0 and newlines <= wanted and len(window) < max_bytes:
            # Clamp to what is left of the cap as well, so max_bytes bounds the
            # read outright instead of only being noticed after a whole block.
            block = min(block_bytes, position, max_bytes - len(window))
            position -= block
            handle.seek(position)
            chunk = handle.read(block)
            if not chunk:
                break
            window[:0] = chunk
            newlines += chunk.count(b"\n")
    except OSError:
        return []
    finally:
        if handle is not None:
            handle.close()

    parts = bytes(window).decode("utf-8", errors="replace").splitlines()
    if position > 0 and len(parts) > 1:
        # The window began mid-file, so its first line can be a partial one.
        parts = parts[1:]
    return parts[-wanted:]
