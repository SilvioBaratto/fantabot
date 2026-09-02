"""Writes for the lega snapshot tables (`adapters/persistence/models/league.py`).

**Append-only, never an upsert — with one recorded exception.** That module's own
docstring states the reason: these are point-in-time captures keyed on `captured_at`,
and the point is the drift between them. Overwriting one in place would be the one thing
this table exists to refuse — a plain `INSERT`, not `ON CONFLICT DO UPDATE`, is what the
shape asks for.

The exception is `league_fixture`. Its key is natural (competition, matchday, the two
teams) and its fields fill in rather than drift: the pairing is fixed in August and the
points arrive when the round is calculated. Snapshotting it would write 144 rows a sync
to record one boolean flipping once per round, so that one table upserts. Every other
method here inserts.

`captured_at` is the table's own `now()` default and is deliberately *not* passed in: one
`lega sync` writes several tables and each row stamps itself, which is why a sync's rows
share a second rather than an identity. When a query needs "the last capture", it asks
for the max per table, not for a shared token.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert

from fantabot.adapters.persistence.models.league import (
    LeagueCompetition,
    LeagueCustomRole,
    LeagueFixture,
    LeaguePlayerPool,
    LeagueSnapshot,
    LeagueTeamSnapshot,
)
from fantabot.adapters.persistence.repositories._base import RepositoryBase
from fantabot.adapters.persistence.upserts import chunked, table_for
from fantabot.domain.lega.models import (
    Competition,
    CustomRole,
    Fixture,
    LeagueState,
    PoolEntry,
    TeamRoster,
)
from fantabot.domain.shared.league import TeamSnapshot


class LeagueRepository(RepositoryBase):
    """Writes for every `league_*` table."""

    def record_team_snapshot(self, snapshot: TeamSnapshot) -> None:
        """Insert one new `league_team_snapshot` row. `captured_at` is the table's own
        `now()` default — every call is a new capture, never a correction of the last."""
        self.session.add(
            LeagueTeamSnapshot(
                league_id=snapshot.league_id,
                team_id=snapshot.team_id,
                user_id=snapshot.user_id,
                nome=snapshot.nome,
                owner=snapshot.owner,
                credits_initial=snapshot.credits_initial,
                credits_spent=snapshot.credits_spent,
                credits_remaining=snapshot.credits_remaining,
            )
        )

    def record_league_state(self, state: LeagueState) -> None:
        """Insert one `league_snapshot`: the matchday and the rules it is played under."""
        self.session.add(
            LeagueSnapshot(
                league_id=state.league_id,
                season_id=state.season_id,
                matchday=state.matchday,
                matchday_start=state.matchday_start,
                budget=state.budget,
                roster_size=state.roster_size,
                active=state.active,
                stopped=state.stopped,
                role_groups=state.role_groups,
                min_roles=list(state.min_roles) or None,
                max_roles=list(state.max_roles) or None,
                modules=list(state.modules) or None,
                bench_size=state.bench_size,
                captain_slots=state.captain_slots,
            )
        )

    def record_team_rosters(self, rosters: Sequence[TeamRoster]) -> int:
        """Insert one `league_team_snapshot` per team, rosa and costs included."""
        for team in rosters:
            self.session.add(
                LeagueTeamSnapshot(
                    league_id=team.league_id,
                    team_id=team.team_id,
                    user_id=team.user_id,
                    nome=team.nome,
                    owner=team.owner,
                    division=team.division,
                    credits_initial=team.credits_initial,
                    credits_spent=team.credits_spent,
                    credits_remaining=team.credits_remaining,
                    roster_ids=[slot.player_id for slot in team.roster],
                    roster_costs=[slot.cost for slot in team.roster],
                )
            )
        return len(rosters)

    def record_competitions(self, competitions: Sequence[Competition]) -> int:
        for comp in competitions:
            self.session.add(
                LeagueCompetition(
                    league_id=comp.league_id,
                    competition_id=comp.competition_id,
                    nome=comp.name,
                    tipo=comp.tipo,
                    start_day=comp.start_day,
                    end_day=comp.end_day,
                    team_ids=list(comp.team_ids),
                    deleted=comp.deleted,
                )
            )
        return len(competitions)

    def record_custom_roles(self, roles: Sequence[CustomRole]) -> int:
        for role in roles:
            self.session.add(
                LeagueCustomRole(
                    league_id=role.league_id,
                    player_id=role.player_id,
                    nome=role.nome,
                    club=role.club,
                    original_role=role.original_role,
                    role=role.role,
                )
            )
        return len(roles)

    def record_pool(self, pool: Sequence[PoolEntry]) -> int:
        """Insert the lega's own player list. `league_player_pool` had no producer until
        this method: the rows in it were captured by hand (`models/league.py`)."""
        for entry in pool:
            self.session.add(
                LeaguePlayerPool(
                    league_id=entry.league_id,
                    player_id=entry.player_id,
                    quotazione=entry.quotazione,
                    fvm_classic=entry.fvm_classic,
                    fvm_mantra=entry.fvm_mantra,
                    ruoli_codice=list(entry.ruoli_codice),
                )
            )
        return len(pool)

    def upsert_fixtures(self, fixtures: Sequence[Fixture]) -> int:
        """Upsert the calendar. The one non-append-only write here; see the module
        docstring. Chunked like the match-grain writes so one statement's parameter list
        stays well inside Postgres's 65,535 bound."""
        rows = [
            {
                "competition_id": f.competition_id,
                "matchday": f.matchday,
                "team_home": f.team_home,
                "team_away": f.team_away,
                "championship_matchday": f.championship_matchday,
                "points_home": f.points_home,
                "points_away": f.points_away,
                "standing_home": f.standing_home,
                "standing_away": f.standing_away,
                "result": f.result,
                "real_result": f.real_result,
                "calculated": f.calculated,
            }
            for f in fixtures
        ]
        if not rows:
            return 0
        table = table_for(LeagueFixture)
        keys = ("competition_id", "matchday", "team_home", "team_away")
        for chunk in chunked(rows):
            statement = insert(table).values(chunk)
            self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=list(keys),
                    set_={
                        column: statement.excluded[column]
                        for column in rows[0]
                        if column not in keys
                    },
                )
            )
        return len(rows)
