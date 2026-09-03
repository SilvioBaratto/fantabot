"""Asta plan — the optimal roster for a lega, on its real (snapshotted) roster rules.

Mirrors `interface/asta.py`'s optimize command: read_plan_inputs -> optimize_roster. The
one difference is the point of S9: for Mantra it injects a RosterRules built from the
lega's latest LeagueSnapshot (size + min roles) instead of the RosterRules(size=30)
default. Read-only; degrades open (found=false on no data / DB error).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PlanPlayer(BaseModel):
    player_id: str
    nome: str
    price: float


class AstaPlan(BaseModel):
    found: bool
    listone: str = ""
    roster_size: int = 0
    total_cost: float = 0.0
    objective: float = 0.0
    budget: float = 0.0
    players: list[PlanPlayer] = []


def build_roster_rules(snapshot: Any) -> Any:
    """A Mantra RosterRules from the lega's snapshot (size + [gk_min, movement_min]).

    Falls back to the default RosterRules() when the snapshot lacks the fields — better
    the default than a crash, but the point is to plan on 25/32 not a hardcoded 30.
    """
    from fantabot.domain.asta.state import RosterRules

    if (
        snapshot is None
        or snapshot.roster_size is None
        or not snapshot.min_roles
        or len(snapshot.min_roles) < 2
    ):
        return RosterRules()
    return RosterRules(
        size=int(snapshot.roster_size),
        min_goalkeepers=int(snapshot.min_roles[0]),
        min_movement=int(snapshot.min_roles[1]),
    )


@router.get("/asta/plan", response_model=AstaPlan, tags=["asta"])
def asta_plan(league_id: int, season: str = "2026/27") -> AstaPlan:
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.asta_planner import read_plan_inputs
    from fantabot.domain.asta.optimizer import optimize_roster
    from fantabot.domain.asta.state import AstaState
    from fantabot.domain.classic.state import ClassicRosterRules

    from fantabot_app.api.reads import league as reads

    try:
        with database_manager.get_session() as session:
            snapshot = reads.latest_settings(session, league_id)
            fmt = "classic" if (snapshot is not None and snapshot.role_groups == 1) else "mantra"
            budget = float(snapshot.budget) if snapshot and snapshot.budget else 500.0

            world = read_plan_inputs(
                session,
                season=season,
                sentiment=None,
                as_of=None,
                tilt_k=1.0,
                listone=fmt,
                num_credits=int(budget),
            )
            if not world.pool:
                return AstaPlan(found=False)

            rules = ClassicRosterRules() if fmt == "classic" else build_roster_rules(snapshot)
            result = optimize_roster(
                AstaState(total_budget=budget),
                world.pool,
                value=world.value,
                prices=world.prices,
                teams=world.teams,
                legality=world.legality,
                rules=rules,
                lam=0.0,
                n_fallbacks=0,
            )

        players = [
            PlanPlayer(
                player_id=pid,
                nome=world.names.get(pid, pid),
                price=float(world.prices.get(pid, 0.0)),
            )
            for pid in result.optimal.player_ids
        ]
        return AstaPlan(
            found=True,
            listone=fmt,
            roster_size=int(getattr(rules, "size", len(players))),
            total_cost=float(result.optimal.total_cost),
            objective=float(result.optimal.objective),
            budget=budget,
            players=players,
        )
    except Exception:  # noqa: BLE001 — degrade open: no data / infeasible / DB error
        return AstaPlan(found=False)
