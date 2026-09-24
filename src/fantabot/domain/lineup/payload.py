"""A `PlannedLineup` into the `gaming/v1/teamLineup` POST body. Pure.

The shape is fixed by what was captured live 2026-09-02 (`docs/leghe-api.md`) — the platform
validates it strictly and answers a missing or misnamed field with a `400`. `swtcMdl` mirrors
`mdl`. `capt` (`[captain, vice]`) and `swtcA`/`swtcB` (a starter, then the reserve who replaces
him first) were captured set on 2026-09-23 and come from the plan; unset, they are `[]` and 0,
which is what saved before either was known. `swtc` was 0 in that capture too, and stays 0.
"""

from __future__ import annotations

from typing import Any

from fantabot.domain.lineup.models import PlannedLineup


def build(plan: PlannedLineup) -> dict[str, Any]:
    """The exact JSON body `apileague.teamLineup_submit` posts for `plan`."""
    return {
        "starts": list(plan.starts),
        "bench": list(plan.bench),
        "capt": list(plan.captains),
        "mdl": plan.module,
        "idcomp": plan.competition,
        "mday": plan.mday,
        "cmday": plan.cmday,
        "tid": plan.tid,
        "allComp": plan.all_comp,
        "visb": True,
        "swtcA": plan.switch[0] if plan.switch else 0,
        "swtcB": plan.switch[1] if plan.switch else 0,
        "swtc": 0,
        "swtcMdl": plan.switch_module or plan.module,
    }
