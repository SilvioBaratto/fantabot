"""Base class for every repository.

A repository takes a ``Session`` and never makes one. That is what keeps the
default test tier socket-free: the suites inject a fake that records the calls
it was asked to make, and no engine is ever built.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


class RepositoryBase:
    """Holds the session a repository was handed."""

    def __init__(self, session: Session) -> None:
        self.session = session
