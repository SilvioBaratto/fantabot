"""Repositories: every query the application makes lives behind one of these."""

from fantabot.adapters.persistence.repositories._base import RepositoryBase
from fantabot.adapters.persistence.repositories.admin import AdminRepository, UnknownTableError
from fantabot.adapters.persistence.repositories.reference import ReferenceRepository
from fantabot.adapters.persistence.repositories.sentiment import (
    SentimentReadRepository,
    SentimentRepository,
)
from fantabot.adapters.persistence.repositories.tokens import UPSERT_COLUMNS, LeagueTokenRepository

__all__ = [
    "UPSERT_COLUMNS",
    "AdminRepository",
    "LeagueTokenRepository",
    "ReferenceRepository",
    "RepositoryBase",
    "SentimentReadRepository",
    "SentimentRepository",
    "UnknownTableError",
]
