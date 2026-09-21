"""Lease handoff to a child the holder itself spawns.

F01: the acceptance path holds the project lease across the whole audit and then
runs ``scripts/verify_provenance.py`` as a child.  That child reads the workflow
database through ``SQLiteStateStore``, which takes the *same* project lease, and
a child can never inherit its parent's ``flock`` implicitly -- so the parent
waited on the child while the child waited on the parent's lock, and the run only
ended at the runner timeout.

The fix hands the held descriptors to that child and nothing more.  These tests
pin both halves: the handoff works, and without it the child really does block.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from factory_core.state_lease import child_lease_handoff, state_commit_lease

REPO_ROOT = Path(__file__).resolve().parents[1]

CHILD_SOURCE = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from factory_core.state_lease import state_commit_lease

    with state_commit_lease(Path(sys.argv[1])):
        print("ACQUIRED")
    """
)


def _run_child(project: Path, *, env: dict, pass_fds: tuple[int, ...] = (), timeout: int = 20):
    return subprocess.run(
        [sys.executable, "-c", CHILD_SOURCE, str(project)],
        env=env,
        pass_fds=pass_fds,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


def test_handoff_is_empty_when_no_lease_is_held(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()

    assert child_lease_handoff(project) == ({}, ())


def test_child_reuses_the_directory_lease_its_parent_holds(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()

    with state_commit_lease(project):
        env, fds = child_lease_handoff(project)
        assert fds, "the holder must offer its descriptors"
        completed = _run_child(project, env={**os.environ, **env}, pass_fds=fds)

    assert completed.returncode == 0, completed.stderr
    assert "ACQUIRED" in completed.stdout


def test_child_reuses_both_directory_and_database_leases(tmp_path):
    """The acceptance path also holds the state database lease."""

    from factory_core.storage import SQLiteStateStore

    project = tmp_path / "demo"
    SQLiteStateStore(project, clock=lambda: 100).initialize(
        project_id="demo", project_type="modeling"
    )

    with state_commit_lease(project):
        env, fds = child_lease_handoff(project)
        assert len(fds) == 2, "both the directory and database leases are held"
        completed = _run_child(project, env={**os.environ, **env}, pass_fds=fds)

    assert completed.returncode == 0, completed.stderr
    assert "ACQUIRED" in completed.stdout


def test_child_without_the_handoff_blocks_on_the_held_lease(tmp_path):
    """Negative control: this is precisely the deadlock, still observable."""

    project = tmp_path / "demo"
    project.mkdir()

    with state_commit_lease(project):
        with pytest.raises(subprocess.TimeoutExpired):
            _run_child(project, env=dict(os.environ), timeout=5)


def test_handoff_does_not_grant_a_lease_the_parent_lacks(tmp_path):
    """After the parent releases, the stale descriptors no longer authorise."""

    project = tmp_path / "demo"
    project.mkdir()

    with state_commit_lease(project):
        env, fds = child_lease_handoff(project)

    # Parent has released; a child given the stale handoff must fall back to
    # ordinary acquisition and simply take the (now free) lock.
    completed = _run_child(project, env={**os.environ, **env}, pass_fds=fds)

    assert completed.returncode == 0, completed.stderr
    assert "ACQUIRED" in completed.stdout
