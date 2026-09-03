"""Lineup preview — the best fieldable formation for a lega. Never submits.

Mirrors interface/lineup.py's plan path: my_team -> teamLineup_read -> lineup_settings ->
inputs_from_lineup -> plan_lineups, then returns the top PlannedLineup. This is the only
read that hits the live platform (apileague, bearer token), so it degrades open to a
reason when there is no key, no token, or no network — and it never calls
teamLineup_submit.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class LineupPlayer(BaseModel):
    player_id: int
    nome: str


class LineupPlan(BaseModel):
    found: bool
    reason: str | None = None
    module: str = ""
    matchday: int | None = None
    starters: list[LineupPlayer] = []
    bench: list[LineupPlayer] = []


def build_lineup_plan(planned: Any, names: dict[int, str]) -> LineupPlan:
    """Map a PlannedLineup + id->name dict to the response (pure)."""
    return LineupPlan(
        found=True,
        module=planned.module,
        matchday=planned.mday,
        starters=[LineupPlayer(player_id=pid, nome=names.get(pid, str(pid))) for pid in planned.starts],
        bench=[LineupPlayer(player_id=pid, nome=names.get(pid, str(pid))) for pid in planned.bench],
    )


@router.get("/lineup/plan", response_model=LineupPlan, tags=["lineup"])
def lineup_plan(league_id: int) -> LineupPlan:
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lineup_planner import inputs_from_lineup, plan_lineups
    from fantabot.config import settings
    from fantabot.domain.lineup.competition import resolve_competition
    from fantabot.domain.tokens.crypto import TokenCipher

    key = settings.fantabot_encryption_key
    if not key:
        return LineupPlan(found=False, reason="No encryption key set — connect an account first.")

    try:
        cipher = TokenCipher(key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            tid = int(apileague.my_team(league_id, store=store)["id"])
            comp = resolve_competition(apileague.competitions(league_id, store=store), tid=tid)
            body = apileague.teamLineup_read(league_id, comp, store=store)
            lineup_conf = apileague.lineup_settings(league_id, store=store)
            rosters = apileague.roster_settings(league_id, store=store)
            fmt = "classic" if int(rosters.get("sroles", 2)) == 1 else "mantra"
            inputs, names = inputs_from_lineup(
                body.get("teamLineupDto", {}),
                body.get("lineUpInfo", []),
                lineup_conf,
                comp,
                tid=tid,
                fmt=fmt,
            )
            plans = plan_lineups(inputs)
        if not plans:
            return LineupPlan(found=False, reason="No fieldable lineup for this lega yet.")
        return build_lineup_plan(plans[0], names)
    except Exception:  # noqa: BLE001 — degrade open: no token / network / incomplete roster
        return LineupPlan(found=False, reason="Not connected, or no lineup available yet.")
