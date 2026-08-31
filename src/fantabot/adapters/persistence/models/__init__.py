"""Every model module is imported here so ``Base.metadata`` is complete.

Alembic's ``env.py`` imports this package and nothing else. A model that is not
re-exported here is invisible to autogenerate, which silently proposes dropping
its table.
"""

from fantabot.adapters.persistence.base import Base, TimestampMixin
from fantabot.adapters.persistence.models.aste import (
    ASTA_TYPES,
    Asta,
    AstaAssignment,
    AstaEvent,
)
from fantabot.adapters.persistence.models.exclusions import PlayerExclusion
from fantabot.adapters.persistence.models.league import (
    LeaguePlayerPool,
    LeagueSnapshot,
    LeagueTeamSnapshot,
)
from fantabot.adapters.persistence.models.matches import COACH_ROLE, MatchGrain
from fantabot.adapters.persistence.models.reference import (
    FONTI,
    LISTONI,
    MACRO_ROLES,
    Player,
    Quotazione,
    Statistica,
    TargetPrice,
    Team,
)
from fantabot.adapters.persistence.models.sentiment import SCORE_COLUMNS, PlayerSentiment
from fantabot.adapters.persistence.models.tokens import FantalabSession, LeagueToken

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
    "FantalabSession",
    "LeaguePlayerPool",
    "LeagueSnapshot",
    "LeagueTeamSnapshot",
    "LeagueToken",
    "MatchGrain",
    "Player",
    "PlayerExclusion",
    "PlayerSentiment",
    "Quotazione",
    "Statistica",
    "TargetPrice",
    "Team",
    "TimestampMixin",
]
