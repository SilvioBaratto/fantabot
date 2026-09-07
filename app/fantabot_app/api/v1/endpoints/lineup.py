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
    #: One of `api/outcomes.LINEUP_PLAN_OUTCOMES`. This route already carried a `reason`,
    #: which made it look solved — but every failure produced the *same* one, "Not
    #: connected, or no lineup available yet", so a network blip and a roster the platform
    #: refuses read identically and only one of them is worth waiting out.
    outcome: str = "planned"
    reason: str | None = None
    module: str = ""
    matchday: int | None = None
    starters: list[LineupPlayer] = []
    bench: list[LineupPlayer] = []


def build_lineup_plan(planned: Any, names: dict[int, str]) -> LineupPlan:
    """Map a PlannedLineup + id->name dict to the response (pure)."""
    return LineupPlan(
        found=True,
        outcome="planned",
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
    from fantabot.domain.tokens.errors import (
        ApiTimeout,
        ApiUnavailable,
        AppKeyRejected,
        TokenError,
        TokenRejected,
    )

    from fantabot_app.api.outcomes import because

    key = settings.fantabot_encryption_key
    if not key:
        return LineupPlan(
            found=False,
            outcome="no_credential",
            reason="No encryption key set — connect an account first.",
        )

    # The order is the order the questions arise, and it is load-bearing: a lega with no
    # stored token must not be reported as a network failure, and a network failure must
    # not be reported as a missing lineup.
    try:
        cipher = TokenCipher(key)
    except Exception as exc:  # noqa: BLE001 — a malformed key is a credential problem
        return LineupPlan(found=False, outcome="no_credential", reason=because(exc))

    try:
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
    # `apileague` maps *every* failure onto `TokenError`, deliberately — no `httpx`
    # exception is re-raised, because both `RequestError.request` and a bare traceback can
    # render the `Authorization` header. That makes a bare `except TokenError` wrong here:
    # it would report a timeout as a credential problem, which is `endpoints/room.py`'s
    # ordering lesson exactly. The three subclass groups are caught most specific first.
    except (TokenRejected, AppKeyRejected) as exc:
        # The platform answered, and said no. A different fact from being unable to ask —
        # a rejected token is not going to resolve by reloading the page.
        return LineupPlan(found=False, outcome="refused", reason=str(exc))
    except (ApiTimeout, ApiUnavailable) as exc:
        return LineupPlan(found=False, outcome="unreachable", reason=str(exc))
    except TokenError as exc:
        # Nothing stored, stored under another key, expired, or for another lega. Each
        # says so in its own words and each names something the operator does by hand.
        return LineupPlan(found=False, outcome="no_credential", reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — the last named outcome, not a catch-all
        return LineupPlan(found=False, outcome="unreachable", reason=because(exc))

    if not plans:
        # There is a roster and the platform is reachable; no eleven of it is fieldable.
        # `plan_lineups` builds only on natural roles, so this means the rosa genuinely
        # cannot fill a module — not that the request failed.
        return LineupPlan(
            found=False,
            outcome="no_lineup",
            reason="No fieldable lineup for this lega yet — the rosa fills no allowed module.",
        )
    return build_lineup_plan(plans[0], names)
