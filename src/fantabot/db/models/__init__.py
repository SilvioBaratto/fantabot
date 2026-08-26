"""Every model module is imported here so ``Base.metadata`` is complete.

Alembic's ``env.py`` imports this package and nothing else. A model that is not
re-exported here is invisible to autogenerate, which silently proposes dropping
its table.
"""

from fantabot.db.base import Base, TimestampMixin
from fantabot.db.models.matches import COACH_ROLE, BonusMalus, Voto
from fantabot.db.models.reference import (
    FONTI,
    LISTONI,
    MACRO_ROLES,
    Player,
    QiBias,
    Quotazione,
    Statistica,
    TargetPrice,
    Team,
)

__all__ = [
    "COACH_ROLE",
    "FONTI",
    "LISTONI",
    "MACRO_ROLES",
    "Base",
    "BonusMalus",
    "Player",
    "QiBias",
    "Quotazione",
    "Statistica",
    "TargetPrice",
    "Team",
    "TimestampMixin",
    "Voto",
]
