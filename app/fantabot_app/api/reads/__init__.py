"""Read-only queries over fantabot's ORM models.

fantabot's LeagueRepository is write-only (append-only snapshots), so the app reads the
latest capture directly from the ORM models here — the same ``max(captured_at)`` pattern
``interface/lega.py::_show`` uses, but selecting rows rather than counts.
"""
