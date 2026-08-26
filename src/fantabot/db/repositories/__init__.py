"""Repositories: every query the application makes lives behind one of these."""

from fantabot.db.repositories._base import RepositoryBase
from fantabot.db.repositories.admin import AdminRepository, UnknownTableError
from fantabot.db.repositories.reference import QuotazioneRow, ReferenceRepository
from fantabot.db.repositories.runtime import RuntimeRepository
from fantabot.db.repositories.sentiment import SentimentReadRepository, SentimentRepository

__all__ = [
    "AdminRepository",
    "QuotazioneRow",
    "ReferenceRepository",
    "RepositoryBase",
    "RuntimeRepository",
    "SentimentReadRepository",
    "SentimentRepository",
    "UnknownTableError",
]
