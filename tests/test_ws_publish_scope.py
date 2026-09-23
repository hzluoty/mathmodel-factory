"""Delivery scoping for the realtime push layer.

Two gaps are pinned here:

* **Cross-user metadata leak.**  An unscoped ``broadcast`` sent project action
  metadata to every connected account, including accounts with no access to the
  project.  Every publish path must now state its audience.
* **Stale identity.**  A WebSocket authenticated from a ticket kept using the
  identity captured at handshake, so disabling an account did not stop delivery.
  The account is re-read at send time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from web.backend.auth_store import AuthStore
from web.backend.config import Settings
from web.backend.ws import ConnectionManager


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        jwt_secret="0123456789abcdef0123456789abcdef",
        admin_password="correct horse battery staple 42",
        auth_db_file=tmp_path / "auth.db",
    )


def _store(settings: Settings) -> AuthStore:
    store = AuthStore(settings.resolved_auth_db_file)
    store.initialize()
    store.bootstrap_admin(settings.admin_password)
    for name in ("alice", "bob"):
        store.register_user(name, f"{name} password", "")
        store.approve_user(name, actor="admin")
    return store


def _project(tmp_path: Path, name: str) -> None:
    project = tmp_path / "ongoing" / name
    project.mkdir(parents=True)
    (project / "checkpoint.md").write_text("- step 1\n", encoding="utf-8")


class _Socket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed = None
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code=None) -> None:
        self.closed = code

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _connect(manager: ConnectionManager, username: str, role: str = "user") -> _Socket:
    socket = _Socket()
    asyncio.run(
        manager.connect(
            socket, {"sub": username, "role": role, "status": "active"}
        )
    )
    return socket


def test_project_publish_does_not_reach_unauthorised_user(tmp_path):
    settings = _settings(tmp_path)
    store = _store(settings)
    _project(tmp_path, "owned")
    _project(tmp_path, "other")
    store.grant_project_owner("owned", "alice", actor="admin")

    manager = ConnectionManager()
    alice = _connect(manager, "alice")
    bob = _connect(manager, "bob")

    asyncio.run(
        manager.publish_project(
            settings, "owned", {"type": "project_action", "project": "owned"}
        )
    )

    assert [message["project"] for message in alice.sent] == ["owned"]
    assert bob.sent == [], "a user without project access must not receive metadata"
    assert bob.closed is None


def test_admin_publish_reaches_only_admins(tmp_path):
    settings = _settings(tmp_path)
    _store(settings)

    manager = ConnectionManager()
    admin = _connect(manager, "admin", role="admin")
    alice = _connect(manager, "alice")

    asyncio.run(manager.publish_admins(settings, {"type": "models_updated"}))

    assert [message["type"] for message in admin.sent] == ["models_updated"]
    assert alice.sent == []


def test_disabled_account_stops_receiving_on_existing_connection(tmp_path):
    settings = _settings(tmp_path)
    store = _store(settings)
    _project(tmp_path, "owned")
    store.grant_project_owner("owned", "alice", actor="admin")

    manager = ConnectionManager()
    alice = _connect(manager, "alice")

    asyncio.run(manager.publish_project(settings, "owned", {"type": "project_action"}))
    assert len(alice.sent) == 1

    store.disable_user("alice", actor="admin")

    asyncio.run(manager.publish_project(settings, "owned", {"type": "project_action"}))

    assert len(alice.sent) == 1, "a disabled account must not keep receiving"
    assert alice.closed is not None, "the revoked connection must be closed"
    assert manager.connections() == []


def test_missing_account_connection_is_revoked(tmp_path):
    settings = _settings(tmp_path)
    _store(settings)

    manager = ConnectionManager()
    ghost = _connect(manager, "ghost")

    asyncio.run(manager.publish_public(settings, {"type": "status_update"}))

    assert ghost.sent == []
    assert ghost.closed is not None
    assert manager.connections() == []


def test_unscoped_broadcast_is_gone():
    assert not hasattr(ConnectionManager, "broadcast"), (
        "an audience-free broadcast is how cross-user leakage happened"
    )
