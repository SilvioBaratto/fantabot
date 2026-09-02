"""One team's credits and identity, from `apileague.fantacalcio.it`. Pure: no I/O.

`GET /onboarding/v1/league/teams/my` and one item of `GET /onboarding/v1/league/teams`
share this row shape (`docs/leghe-api.md`) — abbreviated keys (`id`, `idu`, `n`, `nu`,
`cri`, `crs`, `cr`) that mean nothing to a reader six months from now. `TeamSnapshot` is
that row translated into named fields, and the same shape
`adapters/persistence/models/league.LeagueTeamSnapshot` persists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TeamSnapshot:
    """One team's credits and identity, at the moment it was read."""

    league_id: int
    team_id: int
    user_id: int | None
    nome: str
    owner: str
    credits_initial: int | None
    credits_spent: int | None
    credits_remaining: int | None


def parse_team_snapshot(league_id: int, body: Mapping[str, Any]) -> TeamSnapshot:
    """Translate one `teams/my` (or `teams`-item) body into a `TeamSnapshot`.

    `league_id` is a parameter, not read from the body: the response carries the team id
    (`id`) and its owner (`idu`) but no league id of its own — the caller already knows
    which league it asked, and it is the one thing here that cannot come from anywhere
    else.
    """
    return TeamSnapshot(
        league_id=league_id,
        team_id=int(body["id"]),
        user_id=int(body["idu"]) if body.get("idu") is not None else None,
        nome=str(body.get("n", "")),
        owner=str(body.get("nu", "")),
        credits_initial=body.get("cri"),
        credits_spent=body.get("crs"),
        credits_remaining=body.get("cr"),
    )
