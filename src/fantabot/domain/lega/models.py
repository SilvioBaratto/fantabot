"""The lega's own facts, as records rather than three-letter keys. Pure: no I/O.

Every field here is one the platform actually returns and whose meaning is established
(`docs/leghe-api.md`). Fields whose meaning is a guess are deliberately absent — `d`,
`c`, `bm`, `st` on a team object, `cmod` on the roster settings — because a name is a
claim, and a wrong one outlives the person who guessed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RosterSlot:
    """One owned player and what his owner paid for him."""

    player_id: int
    cost: int


@dataclass(frozen=True)
class TeamRoster:
    """One fanta-team: identity, credits, and the whole rosa.

    The rosa is the point. `GET /onboarding/v1/league/teams` carries it as two
    `;`-joined strings — `cal` (player ids) and `cs` (costs) — positionally paired,
    which is why `parse` refuses a pair whose lengths disagree rather than zipping the
    shorter one and silently mispricing a squad.
    """

    league_id: int
    team_id: int
    user_id: int | None
    nome: str
    owner: str
    division: str
    credits_initial: int | None
    credits_spent: int | None
    credits_remaining: int | None
    roster: tuple[RosterSlot, ...]


@dataclass(frozen=True)
class LeagueState:
    """The lega's settings and where the season has got to.

    Assembled from three endpoints — `league/status`, `league/settings/rosters` and
    `league/settings/lineup` — because no single one of them answers "what are the rules
    here"; the roster size lives in one and the legal modules in another.
    """

    league_id: int
    season_id: int | None
    matchday: int | None
    matchday_start: datetime | None
    active: bool
    stopped: bool
    budget: int | None
    roster_size: int | None
    role_groups: int | None
    min_roles: tuple[int, ...]
    max_roles: tuple[int, ...]
    modules: tuple[str, ...]
    bench_size: int | None
    captain_slots: int | None


@dataclass(frozen=True)
class Competition:
    """One competition inside the lega. The array grows; ids are never pinned."""

    league_id: int
    competition_id: int
    name: str
    tipo: int | None
    start_day: int | None
    end_day: int | None
    team_ids: tuple[int, ...]
    deleted: bool


@dataclass(frozen=True)
class Fixture:
    """One match of one matchday.

    `matchday` is the competition's own numbering and `championship_matchday` is Serie
    A's; they differ from the first round on — this lega's matchday 1 is Serie A's 3 —
    and a lineup submit needs both.
    """

    competition_id: int
    matchday: int
    championship_matchday: int | None
    team_home: int
    team_away: int
    points_home: float | None
    points_away: float | None
    standing_home: int | None
    standing_away: int | None
    result: str | None
    real_result: str | None
    calculated: bool


@dataclass(frozen=True)
class CustomRole:
    """A player whose **Classic** macro role this lega overrode.

    Measured, not assumed: all 27 overrides on 2026-09-02 resolve onto the Classic
    P/D/C/A scale (`parse.CLASSIC_ROLE_CODES`), and every one of them is a move the
    Mantra listone already expresses on its own — Zaccagni C -> A is `W`/`A` there. So
    this is recorded and **not** wired into `domain/asta/legality`: L1 matches Mantra
    codes, and feeding it a Classic macro role would widen a band on no evidence.
    """

    league_id: int
    player_id: int
    nome: str
    club: str
    original_role: str
    role: str


@dataclass(frozen=True)
class PoolEntry:
    """One player as the lega itself lists him — its quotazione and FVM, not ours."""

    league_id: int
    player_id: int
    nome: str
    club: str
    quotazione: int | None
    fvm_classic: int | None
    fvm_mantra: int | None
    ruoli_codice: tuple[str, ...]
