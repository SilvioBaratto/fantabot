"""Per-user filesystem locations, all under a single ``~/.fantabot`` home.

Computed from ``Path.home()`` at call time (not import time) so tests can point
``HOME`` / ``USERPROFILE`` elsewhere, and so the launcher honours a home dir that
changes between processes. Unprivileged on Windows, macOS and Linux — no admin, no
system directory. The Postgres data dir here is what ``pgserver.get_server`` runs
``initdb`` into, and what ``provisioner/postgres.py`` addresses by path.
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


def launch_agents() -> Path:
    """macOS per-user launchd directory: ``~/Library/LaunchAgents``.

    Outside ``~/.fantabot`` because launchd only reads jobs from here. It is the one
    location in this module the app does not own, which is why nothing creates it
    implicitly — ``schedule.install`` does, once, and says so.
    """
    return Path.home() / "Library" / "LaunchAgents"
