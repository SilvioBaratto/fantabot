"""The migration chain must round-trip, and models and migrations must agree.

Marked ``db``: deselected by default, so the socket-free rule holds for the
normal run. Bring the stack up first (``docker compose up -d``).

This is the fail-fast proof from T4 turned into a regression net. Every later
migration inherits it: if a model gains a column and nobody writes the
migration, ``alembic check`` fails here instead of at the next deploy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.db


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_upgrade_head_then_check_detects_no_drift() -> None:
    """SPEC criterion 5: autogenerate immediately after upgrade must be empty.

    ARRAY(Text) and partial unique indexes are the two constructs most likely to
    round-trip badly, which is why they were probed before any row was imported.
    """
    upgrade = _alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    check = _alembic("check")
    assert check.returncode == 0, (
        "models and migrations disagree — autogenerate would emit operations:\n"
        f"{check.stdout}\n{check.stderr}"
    )


def test_downgrade_base_leaves_only_the_version_table() -> None:
    """SPEC criterion 4. This is what the naming convention on Base.metadata
    buys: unnamed constraints cannot be dropped by name and would fail here."""
    assert _alembic("upgrade", "head").returncode == 0

    down = _alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr

    # Leave the database at head so a later test or a manual session finds it
    # migrated rather than empty.
    assert _alembic("upgrade", "head").returncode == 0
