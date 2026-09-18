"""Native write guards and errors; independent of Authority runtime imports."""

from contextlib import contextmanager
import sqlite3

from .domain import InvalidTransition

NATIVE_WRITE_FENCE_ERROR = "AUTHORITY_LEGACY_WRITE_DISABLED"


@contextmanager
def native_write_error_boundary():
    """Translate the guard error after the enclosed transaction rolls back."""
    try:
        yield
    except sqlite3.IntegrityError as exc:
        if str(exc) == NATIVE_WRITE_FENCE_ERROR:
            raise InvalidTransition(
                "Authority owns this database; legacy state writes are disabled"
            ) from exc
        raise
