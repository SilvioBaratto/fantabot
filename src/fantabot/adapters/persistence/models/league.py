"""Point-in-time snapshots of the lega, from apileague.fantacalcio.it.

Append-only and time-stamped, never updated in place: the point is the drift.
``docs/lega-legamiallerotaie2.md`` is one of these captured by hand — 8 teams,
all at 500/500 credits, asta not yet held — and the reason to keep taking them
is the question that snapshot cannot answer on its own: *what did the market
look like before that bid.*

**Every table here has a producer as of 2026-09-02.** `fantabot db snapshot-team` still
writes a single `LeagueTeamSnapshot` for our own team (`apileague.my_team`); `fantabot
lega sync` writes all of them — `LeagueSnapshot`, one `LeagueTeamSnapshot` per team with
the rosa and the costs, `LeaguePlayerPool`, `LeagueCompetition`, `LeagueCustomRole` and
the upserted `LeagueFixture`. The whole-lega read was on SPEC's Non-goals list until then:
what took it off is that `GET /onboarding/v1/league/teams` turned out to carry every
opponent's rosa and purchase prices in two `;`-joined fields, which is the one thing the
asta could not see and the lineup planner will need.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    Text,
    func,
)
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

    # The settings the lega plays by, from `settings/rosters` and `settings/lineup`.
    # They live here rather than in a table of their own because they change with the
    # same event that changes the matchday — an admin editing the lega — and reading
    # "what were the rules on the day we submitted" should be one row, not a join.
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stopped: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    role_groups: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    min_roles: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger), nullable=True)
    max_roles: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger), nullable=True)
    modules: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    bench_size: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    captain_slots: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

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

    # The rosa, and what each player cost. `GET /onboarding/v1/league/teams` carries it
    # as two `;`-joined parallel strings (`cal` ids, `cs` costs); they are stored as two
    # arrays because that is what they are, and because a player id belongs in a column
    # a query can unnest, not inside a string. Every team in the lega is here, not only
    # ours: this is the one read that shows what the opponents bought and paid.
    division: Mapped[str | None] = mapped_column(Text, nullable=True)
    roster_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    roster_costs: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger), nullable=True)

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


class LeagueCompetition(Base):
    """One competition of the lega, at one moment.

    Snapshot-keyed like its siblings for the reason `docs/leghe-api.md` records: the
    array grows and an id in it went stale inside a week, so "which competitions existed
    when we planned" is a question with a date in it.
    """

    __tablename__ = "league_competition"

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    competition_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    nome: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    start_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    end_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    team_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<LeagueCompetition {self.competition_id} @{self.captured_at}>"


class LeagueFixture(Base):
    """One match of the lega's calendar — **upserted**, not snapshotted.

    The exception among these tables, and deliberately so. A fixture is one fact whose
    fields fill in over time: the pairing is fixed at the start of the season and the
    points arrive when the matchday is calculated. Snapshotting it would store 36 rounds
    x 4 matches x every sync for a table whose only interesting transition is
    `calculated` flipping once. So the natural key is the key, and a re-sync updates.

    `matchday` is the competition's numbering; `championship_matchday` is Serie A's. They
    differ from round one (this lega's 1 is Serie A's 3) and the lineup submit needs both.
    """

    __tablename__ = "league_fixture"

    competition_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    matchday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    team_home: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_away: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    championship_matchday: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    points_home: Mapped[float | None] = mapped_column(Float, nullable=True)
    points_away: Mapped[float | None] = mapped_column(Float, nullable=True)
    standing_home: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    standing_away: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    real_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    calculated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<LeagueFixture c={self.competition_id} md={self.matchday}>"


class LeagueCustomRole(Base):
    """A player whose **Classic** macro role this lega overrode.

    Small (27 rows on 2026-09-02) and, for this Mantra lega, informational: the scale is
    Classic P/D/C/A (`domain/lega/parse.CLASSIC_ROLE_CODES`), so it does not reach L1 —
    see `domain/lega/models.CustomRole` for why. Snapshot-keyed because an admin can
    change one mid-season and the plan that used the old tag should still be explicable.
    """

    __tablename__ = "league_custom_role"

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    league_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    nome: Mapped[str] = mapped_column(Text, nullable=False)
    club: Mapped[str] = mapped_column(Text, nullable=False)
    original_role: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<LeagueCustomRole {self.player_id} {self.original_role}->{self.role}>"
