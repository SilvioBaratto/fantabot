"""Every model module is imported here so ``Base.metadata`` is complete.

Alembic's ``env.py`` imports this package and nothing else. A model that is not
re-exported here is invisible to autogenerate, which silently proposes dropping
its table.
"""

from fantabot.db.base import Base, TimestampMixin
from fantabot.db.models.aste import (
    ASTA_TYPES,
    Asta,
    AstaAssignment,
    AstaEvent,
)
from fantabot.db.models.league import (
    LeaguePlayerPool,
    LeagueSnapshot,
    LeagueTeamSnapshot,
)
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
from fantabot.db.models.sentiment import SCORE_COLUMNS, PlayerSentiment
from fantabot.db.models.tokens import FantalabSession, LeagueToken

__all__ = [
    "ASTA_TYPES",
    "COACH_ROLE",
    "FONTI",
    "LISTONI",
    "MACRO_ROLES",
    "SCORE_COLUMNS",
    "Asta",
    "AstaAssignment",
    "AstaEvent",
    "Base",
    "BonusMalus",
    "FantalabSession",
    "LeaguePlayerPool",
    "LeagueSnapshot",
    "LeagueTeamSnapshot",
    "LeagueToken",
    "Player",
    "PlayerSentiment",
    "QiBias",
    "Quotazione",
    "Statistica",
    "TargetPrice",
    "Team",
    "TimestampMixin",
    "Voto",
]
