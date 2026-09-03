"""Database access for the local FastAPI adapter — a thin seam over fantabot's engine.

There is deliberately **no** engine or sessionmaker in this module. Every DB session in
the app comes from fantabot's single lazy ``DatabaseManager``, so the whole process holds
exactly one engine (SPEC A6). The URL is fantabot's own
(``config.settings.fantabot_database_url``), which the launcher points at the provisioned
Postgres by exporting ``FANTABOT_DATABASE_URL``.
"""

from __future__ import annotations

from collections.abc import Generator

from fantabot.adapters.persistence import database_manager
from sqlalchemy.orm import Session


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a fantabot session.

    The session commits on a clean request and rolls back (and re-raises) if the handler
    errors — the semantics of ``database_manager.get_session``. DB-backed path operations
    are sync ``def`` so FastAPI runs them in a threadpool with this sync ``Session``.
    """
    with database_manager.get_session() as session:
        yield session
