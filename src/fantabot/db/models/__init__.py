"""Every model module is imported here so ``Base.metadata`` is complete.

Alembic's ``env.py`` imports this package and nothing else. A model that is not
re-exported here is invisible to autogenerate, which silently proposes dropping
its table.
"""

from fantabot.db.base import Base, TimestampMixin
from fantabot.db.models.matches import ProbeMatchGrain
from fantabot.db.models.reference import Player, Team

__all__ = [
    "Base",
    "Player",
    "ProbeMatchGrain",
    "Team",
    "TimestampMixin",
]
