"""The database shell: engine, models, repositories, importers.

Everything in this package is I/O. Decision logic stays in the pure modules —
``strategy.py``, ``news/models|mantra|prompt|pool``, ``mantra_grid/gates.py`` —
per CLAUDE.md's working rules.

Importing this package must never construct an Engine or open a socket:
``fantabot auth`` has to work with the compose stack down, and the default test
run has to stay socket-free.
"""

from fantabot.db.base import Base, TimestampMixin

__all__ = [
    "Base",
    "TimestampMixin",
]
