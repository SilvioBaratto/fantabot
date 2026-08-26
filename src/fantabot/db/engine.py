"""Engine and session lifecycle.

Modelled on ``optimizer/ingestion``'s daemon rather than a request/response API
(SPEC assumption 2): there is no request scope here, so sessions come from a
context manager rather than a dependency, and nothing is per-request.

Two properties are load-bearing:

* **Lazy.** The engine is built on the first ``get_session()``, never at import.
  ``fantabot auth`` must work with the compose stack down, and the default test
  run must open zero sockets.
* **Injectable.** ``session_factory`` can be supplied, so the repository and
  importer suites exercise transaction behaviour against a fake with no
  database anywhere near them.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool


class DatabaseManager:
    """Owns the process's single Engine and hands out transactional sessions."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = session_factory
        self._lock = threading.RLock()

    @property
    def engine(self) -> Engine | None:
        """The Engine, or ``None`` if nothing has needed one yet."""
        return self._engine

    def _factory(self) -> sessionmaker[Session]:
        """Build the engine on first use. Thread-safe, idempotent."""
        with self._lock:
            if self._session_factory is not None:
                return self._session_factory

            from fantabot.config import settings

            self._engine = create_engine(
                settings.fantabot_database_url,
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=10,
                pool_recycle=3600,
                # auction.py's watch_and_bid polls for hours; without pre-ping a
                # connection the server has since dropped surfaces as a failed
                # bid rather than a reconnect.
                pool_pre_ping=True,
                future=True,
                connect_args={
                    "application_name": "fantabot",
                    "connect_timeout": 10,
                },
            )
            self._session_factory = sessionmaker(
                bind=self._engine,
                autoflush=False,
                expire_on_commit=False,
            )
            return self._session_factory

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Yield a session, committing on a clean exit and rolling back on error.

        The exception always propagates: a caller that swallowed it would leave
        an importer reporting success on a transaction that was rolled back.
        """
        session = self._factory()()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Drop the pool. Safe to call when nothing was ever built."""
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
            self._session_factory = None


database_manager = DatabaseManager()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Module-level shorthand for ``database_manager.get_session()``."""
    with database_manager.get_session() as session:
        yield session


__all__: list[str] = ["DatabaseManager", "database_manager", "get_session"]

