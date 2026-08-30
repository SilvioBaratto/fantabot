"""Point-in-time snapshots of the lega, from apileague.fantacalcio.it.

Append-only and time-stamped, never updated in place: the point is the drift.
``docs/lega-legamiallerotaie2.md`` is one of these captured by hand — 8 teams,
all at 500/500 credits, asta not yet held — and the reason to keep taking them
is the question that snapshot cannot answer on its own: *what did the market
look like before that bid.*

**Nothing writes to these yet.** SPEC open question 5 asks whether the capture
ships in this phase, and the producer needs an HTTP client, which is on SPEC's
Ask-first list. The tables exist so the schema is complete and the migration
chain round-trips; the importer is a decision, not an oversight.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, DateTime, Integer, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from fantabot.adapters.persistence.base import Base


class LeagueSnapshot(Base):
    """The lega's own state at one moment: matchday, budget, roster rules."""

    __tablename__ = "league_snapshot"

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    competition_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    season_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    matchday: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    matchday_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    budget: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    roster_size: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    def __repr__(self) -> str:
        return f"<LeagueSnapshot {self.captured_at} league={self.league_id}>"


class LeagueTeamSnapshot(Base):
    """One rival's credits and identity at one moment.

    ``crs`` and ``cr`` are what make an asta legible after the fact: who had
    spent what, and when.
    """

    __tablename__ = "league_team_snapshot"

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    credits_initial: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    credits_spent: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    credits_remaining: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    def __repr__(self) -> str:
        return f"<LeagueTeamSnapshot {self.captured_at} team={self.team_id}>"


class LeaguePlayerPool(Base):
    """The platform's own player list at one moment — 541 rows per capture.

    ``player_id`` deliberately carries **no foreign key** to ``players``. The two
    lists are drawn from different places: ``players`` is seeded from the scraped
    CSVs, this comes from the live API, and they do not have to agree. A
    constraint here would make a snapshot fail because the seed is a week stale,
    which is exactly the drift the table exists to record.
    """

    __tablename__ = "league_player_pool"

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    quotazione: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fvm_classic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fvm_mantra: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ruoli_codice: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    def __repr__(self) -> str:
        return f"<LeaguePlayerPool {self.captured_at} player={self.player_id}>"
