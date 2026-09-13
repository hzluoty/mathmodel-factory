"""Bounded-memory reading and verification of the existing numbers manifest.

The on-disk JSON schema, cell coverage, checksums and matching tolerance are
unchanged. A temporary SQLite index replaces the second in-memory source map.
"""
import bisect
import json
import math
import sqlite3
import tempfile
from pathlib import Path


class JsonReader:
    def __init__(self, handle, chunk_size=65536):
        self.handle = handle
        self.chunk_size = chunk_size
        self.buffer = ""
        self.decoder = json.JSONDecoder()
        self.eof = False

    def fill(self):
        text = self.handle.read(self.chunk_size)
        self.buffer += text
        self.eof = not text

    def peek(self):
        while True:
            self.buffer = self.buffer.lstrip()
            if self.buffer or self.eof:
                return self.buffer[:1]
            self.fill()

    def expect(self, token):
        if self.peek() != token:
            raise ValueError(f"Expected {token!r} in numbers manifest")
        self.buffer = self.buffer[1:]

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer)
                # A number can end at a chunk boundary before its last digit.
                if end == len(self.buffer) and not self.eof:
                    self.fill()
                    continue
                if end < len(self.buffer) and self.buffer[end] not in " \t\r\n,:]}":
                    if not self.eof:
                        self.fill()
                        continue
                    raise ValueError("Invalid JSON value delimiter")
                self.buffer = self.buffer[end:]
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise
                self.fill()

    def keys(self):
        self.expect("{")
        if self.peek() == "}":
            self.expect("}")
            return
        while True:
            key = self.value()
            if not isinstance(key, str):
                raise ValueError("Manifest object keys must be strings")
            self.expect(":")
            yield key
            if self.peek() == "}":
                self.expect("}")
                return
            self.expect(",")


def iter_manifest_records(path, chunk_size=65536):
    with open(path, encoding="utf-8") as handle:
        reader = JsonReader(handle, chunk_size)
        found = False
        for name in reader.keys():
            if name != "sources":
                reader.value()
                continue
            if found:
                raise ValueError("Duplicate sources object")
            found = True
            for source in reader.keys():
                for key in reader.keys():
                    entry = reader.value()
                    if not isinstance(entry, dict) or "value" not in entry or "checksum" not in entry:
                        raise ValueError(f"Invalid manifest entry: {source}::{key}")
                    yield source, key, entry
        if not found or reader.peek():
            raise ValueError("Missing sources object or trailing JSON data")


class Mismatches:
    """Spool failures too: a corrupt workbook must not exhaust RAM."""
    def __init__(self):
        self.file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.count = 0

    def append(self, entry):
        self.file.write(json.dumps(entry) + "\n")
        self.count += 1

    def __len__(self):
        return self.count

    def __iter__(self):
        self.file.seek(0)
        for line in self.file:
            yield tuple(json.loads(line))

    def close(self):
        self.file.close()

    def __del__(self):
        self.close()


def verify_sources(project_dir, paper_numbers, current_records):
    mismatches = Mismatches()
    remaining = sorted(set(item[2] for item in paper_numbers if math.isfinite(item[2])))
    matched = set()
    with tempfile.TemporaryDirectory(prefix="verify-numbers-") as temporary:
        connection = sqlite3.connect(str(Path(temporary) / "sources.sqlite"))
        try:
            connection.execute("PRAGMA cache_size=-16384")
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("CREATE TABLE cells (source INTEGER, key TEXT, checksum TEXT, PRIMARY KEY(source,key)) WITHOUT ROWID")
            source_ids = {}
            batch = []
            for source, key, entry in current_records:
                source_id = source_ids.setdefault(source, len(source_ids))
                batch.append((source_id, key, entry["checksum"]))
                if len(batch) >= 4096:
                    connection.executemany("INSERT OR REPLACE INTO cells VALUES (?,?,?)", batch)
                    batch.clear()
            connection.executemany("INSERT OR REPLACE INTO cells VALUES (?,?,?)", batch)
            connection.commit()
            cursor = connection.cursor()
            for source, key, entry in iter_manifest_records(project_dir / "numbers_manifest.json"):
                row = cursor.execute("SELECT checksum FROM cells WHERE source=? AND key=?", (source_ids.get(source, -1), key)).fetchone()
                if row is None:
                    mismatches.append((source, key, "missing"))
                elif row[0] != entry["checksum"]:
                    mismatches.append((source, key, "checksum"))
                value = entry["value"]
                if remaining and isinstance(value, (int, float)):
                    value = float(value)
                    if math.isfinite(value):
                        tolerance = max(1e-6, abs(value) * 5e-3)
                        # Expand the candidate bounds by one ULP, then use the
                        # original comparison to preserve floating boundaries.
                        left = bisect.bisect_left(remaining, math.nextafter(value-tolerance, -math.inf))
                        right = bisect.bisect_right(remaining, math.nextafter(value+tolerance, math.inf))
                        for paper_value in remaining[left:right]:
                            if abs(value-paper_value) <= tolerance:
                                matched.add(paper_value)
                        remaining[left:right] = [v for v in remaining[left:right] if v not in matched]
                    elif math.isinf(value):
                        # Preserve the legacy comparison, including infinity.
                        for paper_value in remaining:
                            if abs(value-paper_value) <= abs(value)*5e-3:
                                matched.add(paper_value)
                        remaining = [v for v in remaining if v not in matched]
        finally:
            connection.close()
    return mismatches, [entry for entry in paper_numbers if entry[2] not in matched]
