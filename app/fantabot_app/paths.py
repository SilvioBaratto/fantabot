"""Per-user filesystem locations, all under a single ``~/.fantabot`` home.

Computed from ``Path.home()`` at call time (not import time) so tests can point
``HOME`` / ``USERPROFILE`` elsewhere, and so the launcher honours a home dir that
changes between processes. Unprivileged on Windows, macOS and Linux — no admin, no
system directory. The Postgres data dir here is what ``pgserver.get_server`` runs
``initdb`` into (see ``SPEC.md`` §3).
"""

from __future__ import annotations

from pathlib import Path


def home() -> Path:
    """The ``~/.fantabot`` directory (not created here — callers mkdir as needed)."""
    return Path.home() / ".fantabot"


def pgdata() -> Path:
    """Postgres data directory: ``~/.fantabot/pgdata``."""
    return home() / "pgdata"


def logs() -> Path:
    """Log directory: ``~/.fantabot/logs``."""
    return home() / "logs"
