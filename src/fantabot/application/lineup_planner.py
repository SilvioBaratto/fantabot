"""Compose the weekly formation: roster -> value -> best module -> bench -> `PlannedLineup`.

The one place the lineup value model is assembled, mirroring `application/asta_planner` for
the auction. Pure orchestration of the `domain/lineup` pieces: the inputs (roster ids, the
role/fvm maps, the allowed modules, the matchday coordinates) are gathered by the interface
from `apileague` and `quotazioni` and handed in as `LineupInputs`, so this module opens no
socket and reads no clock. The sentiment multiplier is likewise passed in, already computed
against the caller's `as_of`; `effect=None` is the `--no-sentiment` ablation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fantabot.domain.lineup.bench import order_bench
from fantabot.domain.lineup.build import best_lineup
from fantabot.domain.lineup.marle import roles_from_marle
from fantabot.domain.lineup.models import PlannedLineup, assemble_roster
from fantabot.domain.lineup.value import score


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


def inputs_from_lineup(
    dto: Mapping[str, Any],
    lineup_info: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    competition: int,
) -> tuple[LineupInputs, dict[int, str]]:
    """Turn a `teamLineup_read` response + `settings/lineup` into `LineupInputs` and a
    id->name map. Pure.

    The roster, roles and value all come from `lineUpInfo` (`docs/leghe-api.md`): `role` is
    the numeric marle codes, `indexCompare` is the value signal, `plyr` the name. This is
    the source of record because the scraped `quotazioni` ids do not join the league roster.
    """
    roster_ids: list[int] = []
    roles_by_id: dict[int, list[str]] = {}
    value_by_id: dict[int, float] = {}
    names: dict[int, str] = {}
    for row in lineup_info:
        pid = int(row["pid"])
        roster_ids.append(pid)
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
        tid=int(dto.get("tid", 0)),
        bench_size=int(settings.get("tbench", 12)),
    )
    return inputs, names


def plan_lineup(
    inputs: LineupInputs,
    *,
    effect: Mapping[int, float] | None = None,
) -> PlannedLineup:
    """Build the best legal `PlannedLineup` from `inputs`.

    Raises the `domain/lineup` errors (`RosterIncomplete`, `NoFieldableModule`,
    `BenchIncomplete`) unchanged — they name what is missing and the interface reports them.
    """
    roster = assemble_roster(
        inputs.roster_ids,
        roles_by_id=inputs.roles_by_id,
        fvmma_by_id=inputs.fvmma_by_id,
    )
    scores = score(roster, effect=effect)
    module, starts = best_lineup(roster, inputs.modules, value=scores)
    bench = order_bench(roster, starts, value=scores, size=inputs.bench_size)
    return PlannedLineup(
        module=module,
        starts=tuple(starts),
        bench=tuple(bench),
        competition=inputs.competition,
        mday=inputs.mday,
        cmday=inputs.cmday,
        tid=inputs.tid,
    )
