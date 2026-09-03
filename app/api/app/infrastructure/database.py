"""Synchronous SQLAlchemy database setup for the local FastAPI adapter.

A single engine + sessionmaker over the configured database URL, plus the
FastAPI ``get_db`` dependency and ``init_db`` / ``close_db`` lifecycle hooks.
No pooling knobs, health caching or raw-SQL helpers — this is a thin local
adapter that will reuse fantabot's database later.
"""

import logging
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.infrastructure.settings import settings
from app.infrastructure.orm.base import Base

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a Session and close it afterwards."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create any tables declared on ``Base.metadata`` (safe to call repeatedly)."""
    logger.info("Initializing database...")
    Base.metadata.create_all(bind=engine)


def close_db() -> None:
    """Dispose the engine and its connection pool."""
    logger.info("Closing database connections...")
    engine.dispose()
