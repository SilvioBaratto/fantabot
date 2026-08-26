"""The migration chain must round-trip, and models and migrations must agree.

Marked ``db``: deselected by default. Bring the stack up first.

**Everything here runs against a throwaway database, never the working one.**
The round-trip test has to run ``alembic downgrade base``, which drops every
table — against the real database that silently destroys the seed and every
later phase's data. The scratch database is created before the checks and
dropped after, so running the suite costs nothing but time.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DB = "fantabot_migration_scratch"

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def scratch_dsn() -> Generator[str, None, None]:
    """A fresh, empty database that this module owns and destroys."""
    from fantabot.config import settings

    url = make_url(settings.fantabot_database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")

    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))

    try:
        yield url.set(database=SCRATCH_DB).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        admin.dispose()


def _alembic(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "FANTABOT_DATABASE_URL": dsn},
    )


def test_upgrade_head_then_check_detects_no_drift(scratch_dsn: str) -> None:
    """SPEC criterion 5: autogenerate immediately after upgrade must be empty.

    ARRAY(Text) and partial unique indexes are the two constructs most likely to
    round-trip badly, which is why they were probed before any row was imported.
    """
    upgrade = _alembic(scratch_dsn, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr

    check = _alembic(scratch_dsn, "check")
    assert check.returncode == 0, (
        "models and migrations disagree — autogenerate would emit operations:\n"
        f"{check.stdout}\n{check.stderr}"
    )


def test_downgrade_base_leaves_only_the_version_table(scratch_dsn: str) -> None:
    """SPEC criterion 4. This is what the naming convention on Base.metadata
    buys: unnamed constraints cannot be dropped by name and would fail here."""
    assert _alembic(scratch_dsn, "upgrade", "head").returncode == 0

    down = _alembic(scratch_dsn, "downgrade", "base")
    assert down.returncode == 0, down.stderr

    engine = create_engine(scratch_dsn)
    try:
        with engine.connect() as connection:
            remaining = sorted(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY 1"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    assert remaining == ["alembic_version"]


def test_the_working_database_is_never_touched_by_this_module(scratch_dsn: str) -> None:
    """Guards the reason this module owns a scratch database at all."""
    from fantabot.config import settings

    assert SCRATCH_DB in scratch_dsn
    assert make_url(settings.fantabot_database_url).database != SCRATCH_DB
