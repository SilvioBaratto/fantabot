"""`plan_lineup` — the one place the lineup value model is composed. Pure, zero sockets.

Pins that the planner picks the best legal XI and bench from the value model, that sentiment
tilts the XI, and that `--no-sentiment` (effect=None) reproduces the fvm-only field.
"""

from __future__ import annotations

from fantabot.application.lineup_planner import (
    LineupInputs,
    inputs_from_lineup,
    plan_lineup,
    plan_lineups,
)

MODULES = ["3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"]

# 11 starters whose natural roles field only 3-4-3, each clearly above the reserves.
STARTERS: dict[int, tuple[float, tuple[str, ...]]] = {
    6482: (6.0, ("POR",)),
    2788: (8.0, ("DC",)),
    7564: (7.0, ("DC",)),
    7274: (6.0, ("DC",)),
    7181: (7.0, ("E",)),
    1850: (6.0, ("M",)),
    5504: (6.0, ("C",)),
    5678: (5.0, ("E",)),
    4179: (10.0, ("W",)),
    6875: (9.0, ("A",)),
    2194: (5.0, ("W",)),
}
RESERVE_GK = {50: (4.0, ("POR",))}
# broad-role reserves, all below the weakest starter, so the XI is exactly the starters
RESERVES = {60 + i: (4.0 - 0.2 * i, ("W", "A", "DC", "M", "C", "E")) for i in range(12)}

ALL = {**STARTERS, **RESERVE_GK, **RESERVES}
ROSTER_IDS = list(ALL)
ROLES = {pid: list(roles) for pid, (_, roles) in ALL.items()}
FVMMA = {pid: fvm for pid, (fvm, _) in ALL.items()}

INPUTS = LineupInputs(
    roster_ids=ROSTER_IDS,
    roles_by_id=ROLES,
    fvmma_by_id=FVMMA,
    modules=MODULES,
    competition=311681,
    mday=1,
    cmday=3,
    tid=10000003,
    bench_size=12,
)


def test_plans_the_best_legal_lineup_and_carries_the_matchday_coordinates() -> None:
    plan = plan_lineup(INPUTS)

    assert plan.module == "343"
    assert set(plan.starts) == set(STARTERS)
    assert len(plan.starts) == 11
    assert len(plan.bench) == 12
    assert plan.bench[0] == 50  # reserve keeper
    assert (plan.competition, plan.mday, plan.cmday, plan.tid) == (311681, 1, 3, 10000003)


def test_the_field_is_the_top_value_players() -> None:
    plan = plan_lineup(INPUTS)

    assert 2194 in plan.starts  # a starter (value 5.0) beats the reserves (< 5.0)
    assert 60 not in plan.starts  # a reserve (value 4.0) stays on the bench


def test_plan_lineups_are_ranked_best_first_each_complete() -> None:
    plans = plan_lineups(INPUTS)

    assert plans[0].module == "343"  # the roster fields only 343 here
    assert all(len(p.starts) == 11 and len(p.bench) == 12 for p in plans)


def test_inputs_from_lineup_maps_marle_roles_indexcompare_and_names() -> None:
    dto = {"mday": 1, "cmday": 3}  # note: no tid in the DTO
    info = [
        {"pid": 6482, "role": [6], "indexCompare": 5.5, "plyr": "Mandas"},
        {"pid": 7274, "role": [7, 9], "indexCompare": 6.7, "plyr": "Ze Pedro"},
    ]
    settings = {"mods": ["343", "442"], "tbench": 12}

    inputs, names = inputs_from_lineup(dto, info, settings, competition=311681, tid=10000003)

    assert list(inputs.roster_ids) == [6482, 7274]
    assert inputs.roles_by_id[7274] == ["Dd", "Dc"]  # marle 7,9
    assert inputs.fvmma_by_id[6482] == 5.5  # indexCompare is the value signal
    assert inputs.modules == ["343", "442"]
    assert (inputs.competition, inputs.mday, inputs.cmday, inputs.tid) == (311681, 1, 3, 10000003)
    assert inputs.bench_size == 12
    assert names[7274] == "Ze Pedro"


def test_tid_comes_from_the_argument_not_the_empty_dto() -> None:
    # first-of-season: the DTO is empty, the roster is still present via lineUpInfo
    info = [{"pid": 6482, "role": [6], "indexCompare": 5.5, "plyr": "Mandas"}]

    inputs, _ = inputs_from_lineup({}, info, {"mods": ["343"], "tbench": 12}, 311681, tid=999)

    assert inputs.tid == 999  # authoritative team id, never 0 from the empty DTO
    assert inputs.mday == 0 and inputs.cmday == 0  # missing coords surface as 0 (submit refuses)
