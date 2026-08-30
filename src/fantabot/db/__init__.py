"""The database shell: engine, models, repositories.

Everything in this package is I/O. Decision logic stays in the pure modules —
``asta_engine/optimizer|sentiment|value``, ``news/models|mantra|prompt``,
``mantra_grid/gates.py`` —
per CLAUDE.md's working rules.

Importing this package must never construct an Engine or open a socket:
``fantabot --help`` has to work with the compose stack down — and so does
``fantabot login --help``, which is what SC 22 pins — and the default test run
has to stay socket-free.
"""

from fantabot.db.base import Base, TimestampMixin
from fantabot.db.engine import DatabaseManager, database_manager, get_session

__all__ = [
    "Base",
    "DatabaseManager",
    "TimestampMixin",
    "database_manager",
    "get_session",
]
