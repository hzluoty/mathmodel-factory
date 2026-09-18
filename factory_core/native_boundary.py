"""Keep experimental databases out of the Native execution and delivery path.

Authority/Phase runtimes live in a separate source tree. Their databases are
never downgraded, migrated, or treated as ordinary Native state here. The
small read-only boundary also preserves delivery commit serialization and
the existing no-source-WAL-mutation audit contract.
"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from .domain import InvalidTransition
from .state_lease import (
    StateLeaseError,
    isolated_state_snapshot_ro,
    state_commit_lease,
)


class NativeBoundaryError(InvalidTransition, ValueError):
    """A project is not eligible for the Native mainline."""


def require_native_schema(connection: sqlite3.Connection) -> None:
    """Reject even partial experimental schemas without changing the database."""
    objects = connection.execute("SELECT name FROM sqlite_master").fetchall()
    if any(str(row[0]).startswith(("authority_", "phase3_", "phase4_", "phase5_",
                                  "phase6_", "phase7_", "phase8_", "phase9_"))
           for row in objects):
        raise NativeBoundaryError(
            "NATIVE_WORKFLOW_REQUIRED: experimental Authority/Phase database; "
            "use the separated paper_new workspace for inspection or recovery. "
            "No automatic downgrade is supported."
        )


def require_native_project(project: str | Path) -> None:
    """Inspect a private main/WAL copy; absent state is allowed for artifact tools."""
    root = Path(project).resolve()
    database = root / ".factory" / "state.db"
    try:
        with state_commit_lease(root):
            # lstat distinguishes an absent database from an unsafe dangling link.
            try:
                database.lstat()
            except FileNotFoundError:
                return
            with isolated_state_snapshot_ro(database) as connection:
                require_native_schema(connection)
    except (StateLeaseError, sqlite3.Error, OSError) as exc:
        raise NativeBoundaryError(
            "NATIVE_WORKFLOW_REQUIRED: workflow database cannot be inspected safely"
        ) from exc


def native_delivery_projection_allowed(project: str | Path) -> bool:
    """Read eligibility only; a release still needs all normal audit evidence."""
    try:
        require_native_project(project)
        return True
    except NativeBoundaryError:
        return False


def require_delivery_side_effect_authority(
    project: str | Path,
    *,
    operation: str,
    workflow_id: str | None = None,
    run_generation: str | None = None,
) -> None:
    """Reject experimental coordinates; normal acceptance checks remain separate."""
    if operation not in {"acceptance", "release", "submission", "delivery", "completion", "archive"}:
        raise NativeBoundaryError(f"unsupported delivery operation: {operation}")
    if workflow_id is not None or run_generation is not None:
        raise NativeBoundaryError(
            "NATIVE_WORKFLOW_REQUIRED: experimental workflow/run coordinates "
            "are not supported by the Native delivery path"
        )
    require_native_project(project)


@contextmanager
def delivery_side_effect_commit_lease(
    project: str | Path,
    *,
    operation: str,
    workflow_id: str | None = None,
    run_generation: str | None = None,
) -> Iterator[None]:
    """Recheck eligibility under the same inode lease used by final commits."""
    try:
        with state_commit_lease(project):
            require_delivery_side_effect_authority(
                project, operation=operation,
                workflow_id=workflow_id, run_generation=run_generation,
            )
            yield
    except StateLeaseError as exc:
        raise NativeBoundaryError(f"Native {operation} commit lease failed: {exc}") from exc
