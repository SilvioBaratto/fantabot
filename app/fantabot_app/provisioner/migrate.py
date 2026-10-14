"""Run fantabot's alembic migrations against the provisioned database.

fantabot owns the schema and the migrations; the app never defines its own. alembic's
``env.py`` sets ``sqlalchemy.url`` from ``settings.fantabot_database_url``, which reflects
the ``FANTABOT_DATABASE_URL`` the provisioner exports — so call
``PostgresProvisioner.start()`` before this. The runner is injected so the step is
unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def _alembic_ini() -> Path:
    """Locate fantabot's ``alembic.ini`` at the repo root (``src/fantabot`` → parents[2]).

    Asked of the library rather than derived here: the schema check compares the database
    against the scripts this file's upgrade runs, and two derivations of one path are two
    chances for "which migrations" to have two answers.
    """
    from fantabot.adapters.persistence.alembic_scripts import alembic_ini_path

    return Path(alembic_ini_path())


def _default_run(revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(_alembic_ini())), revision)


def upgrade_head(*, run: Callable[[str], None] = _default_run) -> None:
    """Bring the schema to the head revision."""
    run("head")
