"""Regression guard for the live auth-database isolation contract.

Importing ``web/backend/main.py`` calls ``AuthStore.bootstrap_admin``, which
rewrites the admin password in whatever database ``AUTH_DB_FILE`` names.  On
2026-09-20T12:13:33Z that happened while ``AUTH_DB_FILE`` was unset, so a test
import replaced the running dashboard's admin password with a literal committed
in this repository -- the live admin credential became publicly known.

``tests/conftest.py`` now redirects ``AUTH_DB_FILE`` at collection time.  These
tests pin that redirect so it cannot be removed silently.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_AUTH_DB = REPO_ROOT / "web" / "auth.db"


def _db_bytes() -> bytes | None:
    try:
        return LIVE_AUTH_DB.read_bytes()
    except OSError:
        return None


def test_conftest_redirects_the_auth_db_outside_the_repository() -> None:
    configured = os.environ.get("AUTH_DB_FILE")
    assert configured, "conftest must set AUTH_DB_FILE before tests are collected"
    resolved = Path(configured).resolve()
    assert resolved != LIVE_AUTH_DB.resolve()
    assert REPO_ROOT.resolve() not in resolved.parents


def test_importing_the_app_does_not_touch_the_live_auth_db() -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("bcrypt")
    pytest.importorskip("jwt")

    before = _db_bytes()
    os.environ.setdefault("JWT_SECRET", "0123456789abcdef0123456789abcdef")
    os.environ.setdefault("ADMIN_PASSWORD", "isolation-probe-password")
    sys.modules.pop("web.backend.main", None)
    sys.modules.pop("web.backend.app", None)
    importlib.import_module("web.backend.app")

    assert _db_bytes() == before, (
        "importing the backend wrote the repository auth database; "
        "AUTH_DB_FILE must point outside the repository"
    )
