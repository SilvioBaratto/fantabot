"""Every model module is imported here so ``Base.metadata`` is complete.

Alembic's ``env.py`` imports this package and nothing else. A model that is not
re-exported here is invisible to autogenerate, which silently proposes dropping
its table.
"""

from fantabot.db.base import Base, TimestampMixin
from fantabot.db.models.matches import ProbeMatchGrain
from fantabot.db.models.reference import (
    FONTI,
    LISTONI,
    Player,
    Quotazione,
    Statistica,
    Team,
)

__all__ = [
    "FONTI",
    "LISTONI",
    "Base",
    "Player",
    "ProbeMatchGrain",
    "Quotazione",
    "Statistica",
    "Team",
    "TimestampMixin",
]
