"""Repositories: every query the application makes lives behind one of these."""

from fantabot.db.repositories._base import RepositoryBase
from fantabot.db.repositories.admin import AdminRepository, UnknownTableError
from fantabot.db.repositories.reference import QuotazioneRow, ReferenceRepository
from fantabot.db.repositories.sentiment import SentimentReadRepository, SentimentRepository
from fantabot.db.repositories.tokens import UPSERT_COLUMNS, LeagueTokenRepository

__all__ = [
    "UPSERT_COLUMNS",
    "AdminRepository",
    "LeagueTokenRepository",
    "QuotazioneRow",
    "ReferenceRepository",
    "RepositoryBase",
    "SentimentReadRepository",
    "SentimentRepository",
    "UnknownTableError",
]
