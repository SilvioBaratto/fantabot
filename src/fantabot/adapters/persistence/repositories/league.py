"""Writes for the lega snapshot tables (`adapters/persistence/models/league.py`).

**Append-only, never an upsert — with two recorded exceptions.** That module's own
docstring states the reason: these are point-in-time captures keyed on `captured_at`,
and the point is the drift between them. Overwriting one in place would be the one thing
this table exists to refuse — a plain `INSERT`, not `ON CONFLICT DO UPDATE`, is what the
shape asks for.

The exception is `league_fixture`. Its key is natural (competition, matchday, the two
teams) and its fields fill in rather than drift: the pairing is fixed in August and the
points arrive when the round is calculated. Snapshotting it would write 144 rows a sync
to record one boolean flipping once per round, so that one table upserts. Every other
method here inserts.

The second exception is `purge`, which deletes. Append-only describes how a *sync*
writes, so that drift between captures stays visible — it was never a claim that a lega
can never be removed. The app's "Disconnect" removes an account, and leaving its captures
behind would mean a lega the operator has disconnected still appearing on every other
screen. Nothing else deletes from these tables.

`captured_at` is the table's own `now()` default and is deliberately *not* passed in: one
`lega sync` writes several tables and each row stamps itself, which is why a sync's rows
share a second rather than an identity. When a query needs "the last capture", it asks
for the max per table, not for a shared token.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
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

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult


def _rows(result: Any) -> int:
    """`Session.execute` is typed `Result`, which has no `rowcount` — only
    `CursorResult` does. Narrowed explicitly rather than with a `type: ignore`, so a
    change that stops returning a cursor result fails here and not at runtime. Same
    reasoning as `LeagueTokenRepository.delete`."""
    return int(cast("CursorResult[Any]", result).rowcount or 0)


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

    def competition_ids(self, league_id: int) -> list[int]:
        """Every competition this lega has ever been recorded under.

        Two sources, unioned, and both are needed. `league_competition` is what
        `lega sync` writes today. `league_snapshot.competition_id` is a legacy column
        the current writer never sets — but older captures did, so a lega synced before
        `league_competition` existed has its competition recorded only there.

        Neither is filtered by `captured_at`: these are snapshot tables, and a
        competition dropped from the newest capture still owns fixtures written under
        an older one.
        """
        from_competitions = select(LeagueCompetition.competition_id).where(
            LeagueCompetition.league_id == league_id
        )
        from_snapshots = select(LeagueSnapshot.competition_id).where(
            LeagueSnapshot.league_id == league_id,
            LeagueSnapshot.competition_id.is_not(None),
        )
        rows = self.session.execute(from_competitions.union(from_snapshots)).scalars().all()
        return sorted({int(row) for row in rows if row is not None})

    def purge(self, league_id: int) -> dict[str, int]:
        """Delete every stored row belonging to one lega. Returns rows removed per table.

        **Order is load-bearing in exactly one place.** `league_fixture` has no
        `league_id` column — its only route to a lega is `league_competition`. So the
        competition ids are resolved *first*, into Python, before any statement runs.
        Resolve them afterwards and the subquery sees the deletes already applied within
        the transaction, the predicate goes empty, and 144 fixtures become permanently
        unattributable to any lega. Nothing else here has a cross-table reference, so
        the remaining order is free.

        The fixture delete carries a second predicate that should never fire: it
        excludes competitions another lega also claims. The platform stamps one owning
        lega per competition (`lid` on `GET /league/competitions`), so a shared id would
        mean that contract is wrong — better to leave a stranger's calendar alone and
        find out than to delete it silently.

        The token is not touched here: it belongs to `TokenStore`, and the caller
        removes it last so a failure anywhere leaves the operator their recovery handle.
        """
        comp_ids = self.competition_ids(league_id)
        removed: dict[str, int] = {}

        if comp_ids:
            others = select(LeagueCompetition.competition_id).where(
                LeagueCompetition.league_id != league_id
            )
            result = self.session.execute(
                sql_delete(LeagueFixture).where(
                    LeagueFixture.competition_id.in_(comp_ids),
                    LeagueFixture.competition_id.not_in(others),
                )
            )
            removed["league_fixture"] = _rows(result)
        else:
            removed["league_fixture"] = 0

        for model in (
            LeagueCompetition,
            LeagueCustomRole,
            LeaguePlayerPool,
            LeagueTeamSnapshot,
            LeagueSnapshot,
        ):
            result = self.session.execute(
                sql_delete(model).where(model.league_id == league_id)
            )
            removed[str(model.__tablename__)] = _rows(result)

        return removed
