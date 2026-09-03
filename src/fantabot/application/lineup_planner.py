"""Compose the weekly formation: roster -> value -> best module -> bench -> `PlannedLineup`.

The one place the lineup value model is assembled, mirroring `application/asta_planner` for
the auction. Pure orchestration of the `domain/lineup` pieces: the inputs (roster ids, the
role/value maps, the allowed modules, the matchday coordinates) are gathered by the interface
from `apileague`'s `teamLineup` and handed in as `LineupInputs`, so this module opens no
socket and reads no clock. The per-player value is the sourced `indexCompare` (see
`domain/lineup/value`); no sentiment tilt is applied.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fantabot.domain.asta.roles import normalize_roles
from fantabot.domain.classic.roles import normalize_roles as classic_normalize_roles
from fantabot.domain.classic.roles import role_from_fcrle
from fantabot.domain.lineup import schema
from fantabot.domain.lineup.bench import GK_ROLE, order_bench
from fantabot.domain.lineup.build import ranked_lineups
from fantabot.domain.lineup.errors import NoFieldableModule
from fantabot.domain.lineup.marle import roles_from_marle
from fantabot.domain.lineup.models import PlannedLineup, assemble_roster
from fantabot.domain.lineup.value import score

#: The Classic goalkeeper role for the bench's slot 0, the counterpart to Mantra's `POR`.
CLASSIC_GK_ROLE = "P"


@dataclass(frozen=True)
class LineupInputs:
    """Everything the plan needs, already fetched. Assembled by the interface shell."""

    roster_ids: Sequence[int]
    roles_by_id: Mapping[int, Sequence[str]]
    fvmma_by_id: Mapping[int, float]
    modules: Sequence[str]
    competition: int
    mday: int
    cmday: int
    tid: int
    bench_size: int
    #: `"mantra"` or `"classic"` — selects the role source, the slot provider, the normalizer
    #: and the bench keeper role. Defaults to Mantra so existing callers are unchanged.
    fmt: str = "mantra"


def inputs_from_lineup(
    dto: Mapping[str, Any],
    lineup_info: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    competition: int,
    *,
    tid: int,
    fmt: str = "mantra",
) -> tuple[LineupInputs, dict[int, str]]:
    """Turn a `teamLineup_read` response + `settings/lineup` into `LineupInputs` and a
    id->name map. Pure.

    The roster, roles and value all come from `lineUpInfo` (`docs/leghe-api.md`): `role` is
    the numeric marle codes, `indexCompare` is the value signal, `plyr` the name. This is
    the source of record because the scraped `quotazioni` ids do not join the league roster.

    `tid` is passed in from `apileague.my_team` (authoritative) rather than read from `dto`,
    which is empty when the competition has no saved lineup — a state that would otherwise
    submit `tid=0`. The matchday coordinates still come from `dto`; the submit path refuses
    to POST when they are absent (0).
    """
    roster_ids: list[int] = []
    roles_by_id: dict[int, list[str]] = {}
    value_by_id: dict[int, float] = {}
    names: dict[int, str] = {}
    for row in lineup_info:
        pid = int(row["pid"])
        roster_ids.append(pid)
        # Classic reads the single macro role from `fcrle` ({1:P,2:D,3:C,4:A}); Mantra reads the
        # granular marle codes. A Classic row without `fcrle` yields no role and fails closed at
        # assemble_roster rather than being read on the wrong scale.
        if fmt == "classic":
            fcrle = row.get("fcrle")
            roles_by_id[pid] = [role_from_fcrle(fcrle)] if fcrle is not None else []
        else:
            roles_by_id[pid] = roles_from_marle(row.get("role") or [])
        value_by_id[pid] = float(row.get("indexCompare") or 0.0)
        names[pid] = str(row.get("plyr", pid))

    inputs = LineupInputs(
        roster_ids=roster_ids,
        roles_by_id=roles_by_id,
        fvmma_by_id=value_by_id,
        modules=list(settings.get("mods", [])),
        competition=competition,
        mday=int(dto.get("mday", 0)),
        cmday=int(dto.get("cmday", 0)),
        tid=tid,
        bench_size=int(settings.get("tbench", 12)),
        fmt=fmt,
    )
    return inputs, names


def plan_lineups(inputs: LineupInputs) -> list[PlannedLineup]:
    """Every fieldable `PlannedLineup`, best first.

    The submit path walks this list, falling to the next module if the platform rejects one
    (a wrong schema is survived, not fatal). Raises the `domain/lineup` errors
    (`RosterIncomplete`, `NoFieldableModule`, `BenchIncomplete`) unchanged.
    """
    classic = inputs.fmt == "classic"
    roster = assemble_roster(
        inputs.roster_ids,
        roles_by_id=inputs.roles_by_id,
        fvmma_by_id=inputs.fvmma_by_id,
        normalize=classic_normalize_roles if classic else normalize_roles,
    )
    scores = score(roster)
    slots_provider = schema.classic_slots if classic else schema.slots
    gk_role = CLASSIC_GK_ROLE if classic else GK_ROLE
    plans = [
        PlannedLineup(
            module=module,
            starts=tuple(starts),
            bench=tuple(
                order_bench(roster, starts, value=scores, size=inputs.bench_size, gk_role=gk_role)
            ),
            competition=inputs.competition,
            mday=inputs.mday,
            cmday=inputs.cmday,
            tid=inputs.tid,
        )
        for module, starts in ranked_lineups(
            roster, inputs.modules, value=scores, slots_provider=slots_provider
        )
    ]
    if not plans:
        raise NoFieldableModule(tuple(inputs.modules))
    return plans


def plan_lineup(inputs: LineupInputs) -> PlannedLineup:
    """The single best `PlannedLineup` from `inputs`."""
    return plan_lineups(inputs)[0]
