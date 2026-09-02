"""A `PlannedLineup` into the `gaming/v1/teamLineup` POST body. Pure.

The shape is fixed by what was captured live 2026-09-02 (`docs/leghe-api.md`) — the platform
validates it strictly and answers a missing or misnamed field with a `400`. For this phase
`capt` is empty (no captain) and `swtcMdl` mirrors `mdl` (SPEC open Qs 1-2), both confirmed
to save.
"""

from __future__ import annotations

from typing import Any

from fantabot.domain.lineup.models import PlannedLineup


def build(plan: PlannedLineup) -> dict[str, Any]:
    """The exact JSON body `apileague.teamLineup_submit` posts for `plan`."""
    return {
        "starts": list(plan.starts),
        "bench": list(plan.bench),
        "capt": [],
        "mdl": plan.module,
        "idcomp": plan.competition,
        "mday": plan.mday,
        "cmday": plan.cmday,
        "tid": plan.tid,
        "allComp": False,
        "visb": True,
        "swtcA": 0,
        "swtcB": 0,
        "swtc": 0,
        "swtcMdl": plan.module,
    }
