"""Repositories: every query the application makes lives behind one of these."""

from fantabot.db.repositories._base import RepositoryBase
from fantabot.db.repositories.admin import AdminRepository, UnknownTableError

__all__ = [
    "AdminRepository",
    "RepositoryBase",
    "UnknownTableError",
]
