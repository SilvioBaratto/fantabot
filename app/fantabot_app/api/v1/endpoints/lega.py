"""Lega overview and rosters — read from the latest snapshot capture.

The roster rules here (roster_size, min/max roles, bench) are the live, snapshotted
settings — the same ones the asta planner should read instead of the RosterRules(size=30)
default. Both endpoints degrade open (empty on DB error).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class LegaOverview(BaseModel):
    league_id: int
    league_name: str | None = None
    captured_at: datetime | None = None
    matchday: int | None = None
    budget: int | None = None
    roster_size: int | None = None
    min_roles: list[int] | None = None
    max_roles: list[int] | None = None
    modules: list[str] | None = None
    bench_size: int | None = None
    team_count: int = 0


class RosterSlot(BaseModel):
    player_id: int
    cost: int | None = None


class TeamRoster(BaseModel):
    team_id: int
    nome: str
    owner: str
    credits_initial: int | None = None
    credits_spent: int | None = None
    credits_remaining: int | None = None
    roster: list[RosterSlot]


def build_overview(league_id: int, snapshot: Any, team_count: int) -> LegaOverview:
    """Map a LeagueSnapshot (or None) to an overview."""
    if snapshot is None:
        return LegaOverview(league_id=league_id, team_count=team_count)
    return LegaOverview(
        league_id=league_id,
        league_name=None,  # names live on team rows / listone, not the snapshot
        captured_at=snapshot.captured_at,
        matchday=snapshot.matchday,
        budget=snapshot.budget,
        roster_size=snapshot.roster_size,
        min_roles=list(snapshot.min_roles) if snapshot.min_roles is not None else None,
        max_roles=list(snapshot.max_roles) if snapshot.max_roles is not None else None,
        modules=list(snapshot.modules) if snapshot.modules is not None else None,
        bench_size=snapshot.bench_size,
        team_count=team_count,
    )


def build_rosters(teams: list[Any]) -> list[TeamRoster]:
    """Map LeagueTeamSnapshot rows to rosters, zipping the parallel id/cost arrays."""
    result: list[TeamRoster] = []
    for team in teams:
        ids = list(team.roster_ids or [])
        costs = list(team.roster_costs or [])
        roster = [
            RosterSlot(player_id=pid, cost=(costs[i] if i < len(costs) else None))
            for i, pid in enumerate(ids)
        ]
        result.append(
            TeamRoster(
                team_id=team.team_id,
                nome=team.nome,
                owner=team.owner,
                credits_initial=team.credits_initial,
                credits_spent=team.credits_spent,
                credits_remaining=team.credits_remaining,
                roster=roster,
            )
        )
    return result


@router.get("/lega", response_model=list[LegaOverview], tags=["lega"])
def lega_list() -> list[LegaOverview]:
    from fantabot.adapters.persistence import database_manager

    from fantabot_app.api.reads import league as reads

    try:
        with database_manager.get_session() as session:
            overviews = []
            for league_id in reads.all_league_ids(session):
                snapshot = reads.latest_settings(session, league_id)
                teams = reads.latest_rosters(session, league_id)
                overviews.append(build_overview(league_id, snapshot, len(teams)))
        return overviews
    except Exception:  # noqa: BLE001 — degrade open
        return []


@router.get("/lega/{league_id}/rosters", response_model=list[TeamRoster], tags=["lega"])
def lega_rosters(league_id: int) -> list[TeamRoster]:
    from fantabot.adapters.persistence import database_manager

    from fantabot_app.api.reads import league as reads

    try:
        with database_manager.get_session() as session:
            teams = reads.latest_rosters(session, league_id)
        return build_rosters(teams)
    except Exception:  # noqa: BLE001
        return []
