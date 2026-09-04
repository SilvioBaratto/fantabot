"""Environment checks for ``fantabot-app doctor`` - the first thing to run when stuck.

Each check answers one question and never raises: a failure is reported, not thrown, so
``doctor`` always prints a full report. Checks read the real environment (interpreter,
imports, provisioned data dir); they open no sockets.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    return Check("python", version >= (3, 11), text)


def _fantabot() -> Check:
    try:
        import fantabot  # noqa: F401

        return Check("fantabot", True, "importable")
    except Exception as exc:  # noqa: BLE001
        return Check("fantabot", False, f"not importable: {type(exc).__name__}")


def _postgres_wheel() -> Check:
    try:
        import pixeltable_pgserver  # noqa: F401

        return Check("postgres", True, "pixeltable_pgserver wheel present")
    except Exception:  # noqa: BLE001
        return Check("postgres", False, "wheel missing - reinstall the app")


def _database() -> Check:
    from fantabot_app import paths

    if os.environ.get("FANTABOT_DATABASE_URL"):
        return Check("database", True, "FANTABOT_DATABASE_URL set")
    pgdata = paths.pgdata()
    if pgdata.exists():
        return Check("database", True, f"provisioned at {pgdata}")
    return Check("database", False, "not provisioned - run `fantabot-app setup`")


def _chromium() -> Check:
    try:
        import playwright  # noqa: F401

        return Check("chromium", True, "playwright present (run `setup` if login fails)")
    except Exception:  # noqa: BLE001
        return Check("chromium", False, "playwright missing - run `fantabot-app setup`")


def _encryption_key() -> Check:
    from fantabot_app import keyfile

    if os.environ.get(keyfile.ENV_ENCRYPTION_KEY) or keyfile.key_path().exists():
        return Check("encryption key", True, "present")  # never report the key itself
    return Check("encryption key", False, "not set - run `fantabot-app setup`")


def run_checks() -> list[Check]:
    """Run every check and return the results, in report order."""
    return [_python(), _fantabot(), _postgres_wheel(), _database(), _encryption_key(), _chromium()]
